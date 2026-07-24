from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    referral_code: Mapped[Optional[str]] = mapped_column(
        String(32), unique=True, nullable=True, index=True
    )
    referred_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True, index=True
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    traffic_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    original_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    renew_vpn_account_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wallet_debit: Mapped[int] = mapped_column(Integer, default=0)
    referral_discount_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    referral_cashback_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    receipt_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending, approved, rejected
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class WalletTopUp(Base):
    """User wallet top-up request awaiting receipt review."""

    __tablename__ = "wallet_topups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    requested_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    credited_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    receipt_file_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class VPNAccount(Base):
    __tablename__ = "vpn_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=False, unique=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    config_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    subscription_path: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expiry_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    traffic_low_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_renewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PlanConfig(Base):
    """Pre-made VPN config/subscription link assigned to a plan from plans.yaml."""

    __tablename__ = "plan_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    config_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_assigned: Mapped[bool] = mapped_column(Boolean, default=False)
    order_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("orders.id"), nullable=True, index=True
    )
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PanelSettings(Base):
    """Singleton (id=1) — 3x-ui panel connection and provisioning options."""

    __tablename__ = "panel_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    panel_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    panel_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    panel_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subscription_base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    provisioning_mode: Mapped[str] = mapped_column(String(20), default="manual")
    selected_inbound_ids: Mapped[str] = mapped_column(Text, default="[]")
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
