import asyncio

import httpx

from app.config.settings import settings
from app.utils.logger import logger
from app.xui.client import XUIClient


async def poll_and_process_jobs() -> None:
    """Run on Iran server: poll bot API and create clients on localhost 3x-ui."""
    if not settings.BOT_API_URL or not settings.WORKER_SECRET:
        raise ValueError("BOT_API_URL and WORKER_SECRET are required for the worker")

    if not settings.XUI_URL:
        raise ValueError("XUI_URL must be set to localhost panel URL on the worker server")

    headers = {"Authorization": f"Bearer {settings.WORKER_SECRET}"}
    base = settings.BOT_API_URL.rstrip("/")

    logger.info(f"Remote worker started. Bot API: {base}, Panel: {settings.XUI_URL}")

    async with httpx.AsyncClient(verify=False, timeout=60.0) as http:
        while True:
            try:
                response = await http.get(f"{base}/worker/jobs/next", headers=headers)
                if response.status_code == 401:
                    logger.error("Worker authentication failed. Check WORKER_SECRET.")
                    await asyncio.sleep(30)
                    continue

                response.raise_for_status()
                payload = response.json()
                job = payload.get("job")
                if not job:
                    await asyncio.sleep(5)
                    continue

                panel = payload.get("panel", {})
                public_url = settings.XUI_PUBLIC_URL or panel.get("public_url") or settings.XUI_URL

                client = XUIClient(
                    base_url=settings.XUI_URL,
                    username=panel["username"],
                    password=panel["password"],
                    inbound_id=panel["inbound_id"],
                    public_base_url=public_url,
                )

                email = f"tg_{job['user_id']}"
                result = await client.add_client(
                    email=email,
                    traffic_gb=job["traffic_gb"],
                    expire_days=job["days"],
                )

                job_id = job["id"]
                if not result:
                    await http.post(
                        f"{base}/worker/jobs/{job_id}/fail",
                        headers=headers,
                        json={"error": "add_client failed on local panel"},
                    )
                    logger.error(f"Failed to provision job {job_id}")
                    continue

                await http.post(
                    f"{base}/worker/jobs/{job_id}/complete",
                    headers=headers,
                    json={
                        "xui_client_id": result["uuid"],
                        "subscription_url": result["subscription_url"],
                    },
                )
                logger.info(f"Provisioned job {job_id} for user {job['user_id']}")

            except Exception as exc:
                logger.error(f"Worker loop error: {exc}")
                await asyncio.sleep(10)


async def main() -> None:
    await poll_and_process_jobs()


if __name__ == "__main__":
    asyncio.run(main())
