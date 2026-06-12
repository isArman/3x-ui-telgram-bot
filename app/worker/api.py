import json
from datetime import datetime

from aiohttp import web
from sqlalchemy import select

from app.config.settings import settings
from app.database.models import Payment, ProvisionJob
from app.database.session import AsyncSessionLocal, init_db
from app.utils.logger import logger
from app.xui.service import claim_next_provision_job, get_effective_panel_config


def _authorized(request: web.Request) -> bool:
    if not settings.WORKER_SECRET:
        return False
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {settings.WORKER_SECRET}"


def _unauthorized():
    return web.json_response({"error": "unauthorized"}, status=401)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def worker_next_job(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _unauthorized()

    async with AsyncSessionLocal() as session:
        job = await claim_next_provision_job(session)
        if not job:
            return web.json_response({"job": None})

        config = await get_effective_panel_config(session)
        return web.json_response(
            {
                "job": {
                    "id": job.id,
                    "payment_id": job.payment_id,
                    "order_id": job.order_id,
                    "user_id": job.user_id,
                    "days": job.days,
                    "traffic_gb": job.traffic_gb,
                },
                "panel": {
                    "username": config.username,
                    "password": config.password,
                    "inbound_id": config.inbound_id,
                    "public_url": config.subscription_base_url,
                },
            }
        )


async def worker_complete_job(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _unauthorized()

    job_id = int(request.match_info["job_id"])
    body = await request.json()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ProvisionJob).where(ProvisionJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return web.json_response({"error": "job not found"}, status=404)

        job.status = "completed"
        job.xui_client_id = body.get("xui_client_id", "")
        job.subscription_url = body.get("subscription_url", "")
        job.completed_at = datetime.utcnow()
        await session.commit()

    logger.info(f"Provision job {job_id} completed by remote worker")
    return web.json_response({"success": True})


async def worker_fail_job(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _unauthorized()

    job_id = int(request.match_info["job_id"])
    body = await request.json()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ProvisionJob).where(ProvisionJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return web.json_response({"error": "job not found"}, status=404)

        job.status = "failed"
        job.error_message = body.get("error", "unknown error")
        job.completed_at = datetime.utcnow()
        await session.commit()

        payment_result = await session.execute(select(Payment).where(Payment.id == job.payment_id))
        payment = payment_result.scalar_one_or_none()
        if payment and payment.status == "processing":
            payment.status = "pending"

        await session.commit()

    logger.error(f"Provision job {job_id} failed: {body.get('error')}")
    return web.json_response({"success": True})


def create_worker_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/worker/health", health)
    app.router.add_get("/worker/jobs/next", worker_next_job)
    app.router.add_post("/worker/jobs/{job_id}/complete", worker_complete_job)
    app.router.add_post("/worker/jobs/{job_id}/fail", worker_fail_job)
    return app


async def start_worker_api() -> web.AppRunner:
    await init_db()
    app = create_worker_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.WORKER_API_HOST, settings.WORKER_API_PORT)
    await site.start()
    logger.info(f"Worker API listening on {settings.WORKER_API_HOST}:{settings.WORKER_API_PORT}")
    return runner
