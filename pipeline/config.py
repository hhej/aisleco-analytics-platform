"""Settings (environment variables, defaults for the local Mac) and the CRM tables the pipeline reads (Spec 01 §2.2)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = os.getenv


@dataclass(frozen=True)
class Config:
    crm_dsn: str = field(default_factory=lambda: env("CRM_DSN", "dbname=aisleco_crm"))
    dw_dsn: str = field(default_factory=lambda: env("DW_DSN", "dbname=aisleco_dw"))
    admin_dsn: str = field(default_factory=lambda: env("ADMIN_DSN", "dbname=postgres"))
    lake_dir: Path = field(default_factory=lambda: Path(env("LAKE_DIR", ROOT / "lake")))
    overlap_seconds: int = field(default_factory=lambda: int(env("ETL_OVERLAP_SECONDS", "300")))   # late-commit overlap
    connect_retries: int = field(default_factory=lambda: int(env("ETL_CONNECT_RETRIES", "4")))
    retry_base_seconds: float = field(default_factory=lambda: float(env("ETL_RETRY_BASE_SECONDS", "1")))
    extract_workers: int = field(default_factory=lambda: int(env("ETL_EXTRACT_WORKERS", "4")))
    max_reject_rate: float = field(default_factory=lambda: float(env("ETL_MAX_REJECT_RATE", "0.05")))   # rule S01
    min_rows_for_rate: int = field(default_factory=lambda: int(env("ETL_MIN_ROWS_FOR_RATE", "20")))
    # batch cap: changed transaction rows per incremental run; the rest waits in the CRM (the queue). 0 = no cap
    max_rows_per_run: int = field(default_factory=lambda: int(env("ETL_MAX_ROWS_PER_RUN", "100000")))
    fail_at: str = field(default_factory=lambda: env("ETL_FAIL_AT", ""))          # test hook: step that crashes before commit
    offline: bool = field(default_factory=lambda: env("ETL_OFFLINE", "") == "1")   # use the cached holiday calendar

    @property
    def bronze(self):
        return self.lake_dir / "bronze"


@dataclass(frozen=True)
class Source:
    table: str
    schema: str
    key: str
    kind: str                 # incremental | snapshot | restricted
    columns: tuple = ()       # empty = all columns
    parents: tuple = ()       # (column, parent table): parents of a taken row are pulled into the batch

    @property
    def folder(self):
        return "crm_restricted" if self.kind == "restricted" else "crm"


SOURCES = (
    Source("MarketingChannel", "master", "ChannelID", "snapshot"),
    Source("Package", "master", "PackageID", "snapshot"),
    Source("EmployeeRole", "master", "RoleID", "snapshot"),
    Source("Employee", "master", "EmployeeID", "snapshot"),
    Source("EmployeeContract", "master", "ContractID", "snapshot"),
    # personal data: only the columns the Sales list and matching need (Spec 01 §6)
    Source("CustomerContact", "master", "CustomerContactID", "restricted",
           ("CustomerContactID", "CustomerID", "ContactName", "Phone", "IsPrimaryContact", "UpdatedAt")),
    Source("CustomerChannelAccount", "master", "CustomerAccountID", "restricted",
           ("CustomerAccountID", "CustomerID", "Platform", "DisplayName", "FirstSeenAt", "UpdatedAt")),
    Source("Inquiry", "transaction", "InquiryID", "incremental"),
    Source("ChatMessage", "transaction", "ChatMessageID", "incremental", (), (("InquiryID", "Inquiry"),)),
    Source("Event", "transaction", "EventID", "incremental", (), (("InquiryID", "Inquiry"),)),
    Source("EventEmployeeAssignment", "transaction", "EventEmployeeAssignmentID", "incremental", (), (("EventID", "Event"),)),
    Source("SalesActivity", "transaction", "ActivityID", "incremental", (), (("InquiryID", "Inquiry"),)),
)
SOURCE_BY_TABLE = {s.table: s for s in SOURCES}
