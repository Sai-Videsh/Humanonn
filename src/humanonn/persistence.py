from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency during local dev
    psycopg = None
    dict_row = None


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None
    return None


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _safe_public_base_url(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.rstrip("/")


def _storage_object_url(base_url: str, bucket: str, object_path: str, public: bool) -> str:
    quoted_path = "/".join(urllib.parse.quote(part, safe="") for part in object_path.split("/"))
    if public:
        return f"{base_url}/storage/v1/object/public/{bucket}/{quoted_path}"
    return f"{base_url}/storage/v1/object/{bucket}/{quoted_path}"


@dataclass(frozen=True)
class PersistenceConfig:
    database_url: str | None
    supabase_url: str | None
    supabase_service_role_key: str | None
    storage_bucket: str
    storage_public: bool
    storage_prefix: str

    @classmethod
    def from_env(cls) -> "PersistenceConfig":
        try:
            from humanonn.config import _load_dotenv
            _load_dotenv()
        except ImportError:
            pass
        is_prod = (os.getenv("HUMANONN_PRODUCTION") or os.getenv("PRODUCTION") or "false").strip().lower() == "true"
        db_var = "DATABASE_URL_PROD" if is_prod else "DATABASE_URL"
        database_url = (os.getenv(db_var) or os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or "").strip() or None
        supabase_url = (os.getenv("SUPABASE_URL") or "").strip() or None
        service_role_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip() or None
        storage_bucket = (os.getenv("HUMANONN_STORAGE_BUCKET") or "humanonn-scans").strip()
        storage_public = (os.getenv("HUMANONN_STORAGE_PUBLIC") or "false").strip().lower() == "true"
        storage_prefix = (os.getenv("HUMANONN_STORAGE_PREFIX") or "humanonn/scans").strip().strip("/")
        return cls(
            database_url=database_url,
            supabase_url=_safe_public_base_url(supabase_url),
            supabase_service_role_key=service_role_key,
            storage_bucket=storage_bucket,
            storage_public=storage_public,
            storage_prefix=storage_prefix,
        )


class SupabaseStorageClient:
    def __init__(self, config: PersistenceConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.supabase_url and self.config.supabase_service_role_key and self.config.storage_bucket)

    def public_url(self, object_path: str) -> str | None:
        if not self.config.supabase_url:
            return None
        return _storage_object_url(self.config.supabase_url, self.config.storage_bucket, object_path, True)

    def upload_bytes(self, object_path: str, data: bytes, content_type: str | None = None) -> str | None:
        if not self.enabled:
            return None

        content_type = content_type or "application/octet-stream"
        url = f"{self.config.supabase_url}/storage/v1/object/{self.config.storage_bucket}/{urllib.parse.quote(object_path, safe='/')}"
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.supabase_service_role_key}",
                "apikey": self.config.supabase_service_role_key,
                "x-upsert": "true",
                "content-type": content_type,
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
        return self.public_url(object_path) if self.config.storage_public else _storage_object_url(
            self.config.supabase_url,
            self.config.storage_bucket,
            object_path,
            False,
        )

    def upload_file(self, object_path: str, path: Path) -> str | None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.upload_bytes(object_path, path.read_bytes(), content_type=content_type)

    def upload_directory_bundle(self, object_path: str, directory: Path) -> str | None:
        if not directory.exists():
            return None
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            temp_zip = Path(handle.name)
        try:
            with zipfile.ZipFile(temp_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file_path in sorted(directory.rglob("*")):
                    if file_path.is_file():
                        archive.write(file_path, arcname=str(file_path.relative_to(directory)))
            return self.upload_file(object_path, temp_zip)
        finally:
            try:
                temp_zip.unlink(missing_ok=True)
            except Exception:
                pass


class PostgresJobStore:
    TABLE_NAME = "humanonn_scan_jobs"
    ARTIFACTS_TABLE_NAME = "humanonn_job_artifacts"

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @property
    def enabled(self) -> bool:
        return bool(self.database_url and psycopg is not None)

    def _connect(self):
        if not self.enabled:
            raise RuntimeError("Postgres persistence is not configured.")
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def ensure_schema(self) -> None:
        if not self.enabled:
            return

        cleanup_old_schema = f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='{self.TABLE_NAME}' AND column_name='screenshot_bytes'
            ) THEN
                DROP TABLE IF EXISTS {self.ARTIFACTS_TABLE_NAME} CASCADE;
                DROP TABLE IF EXISTS {self.TABLE_NAME} CASCADE;
            END IF;
        END $$;
        """

        ddl_jobs = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
            job_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            repo_url TEXT,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            started_at TIMESTAMPTZ,
            done_at TIMESTAMPTZ,
            return_code INTEGER,
            error TEXT,
            live_logs JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_logs JSONB NOT NULL DEFAULT '[]'::jsonb,
            report JSONB,
            artifact_root TEXT,
            storage_prefix TEXT,
            screenshot_url TEXT,
            main_image_url TEXT,
            artifact_bundle_url TEXT,
            artifact_manifest_url TEXT,
            storage_meta JSONB NOT NULL DEFAULT '{{}}'::jsonb
        );
        """

        ddl_artifacts = f"""
        CREATE TABLE IF NOT EXISTS {self.ARTIFACTS_TABLE_NAME} (
            job_id TEXT PRIMARY KEY REFERENCES {self.TABLE_NAME}(job_id) ON DELETE CASCADE,
            screenshot_bytes BYTEA,
            site_json JSONB,
            manifest_json JSONB,
            artifact_bundle_bytes BYTEA,
            storage_meta JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL
        );
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(cleanup_old_schema)
                cur.execute(ddl_jobs)
                cur.execute(ddl_artifacts)
            conn.commit()

    def cleanup_stuck_jobs(self) -> None:
        if not self.enabled:
            return
        sql = f"""
        UPDATE {self.TABLE_NAME}
        SET status = 'failed',
            error = 'Worker restarted while job was running.',
            done_at = NOW(),
            updated_at = NOW()
        WHERE status IN ('running', 'queued')
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    def upsert(self, snapshot: dict[str, Any]) -> None:
        if not self.enabled:
            return

        sql_jobs = f"""
        INSERT INTO {self.TABLE_NAME} (
            job_id, url, repo_url, mode, status, created_at, updated_at,
            started_at, done_at, return_code, error, live_logs, source_logs,
            report, artifact_root, storage_prefix, screenshot_url,
            main_image_url, artifact_bundle_url, artifact_manifest_url, storage_meta
        ) VALUES (
            %(job_id)s, %(url)s, %(repo_url)s, %(mode)s, %(status)s, %(created_at)s, %(updated_at)s,
            %(started_at)s, %(done_at)s, %(return_code)s, %(error)s, %(live_logs)s::jsonb, %(source_logs)s::jsonb,
            %(report)s::jsonb, %(artifact_root)s, %(storage_prefix)s, %(screenshot_url)s,
            %(main_image_url)s, %(artifact_bundle_url)s, %(artifact_manifest_url)s, %(storage_meta)s::jsonb
        )
        ON CONFLICT (job_id) DO UPDATE SET
            url = EXCLUDED.url,
            repo_url = EXCLUDED.repo_url,
            mode = EXCLUDED.mode,
            status = EXCLUDED.status,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at,
            started_at = EXCLUDED.started_at,
            done_at = EXCLUDED.done_at,
            return_code = EXCLUDED.return_code,
            error = EXCLUDED.error,
            live_logs = EXCLUDED.live_logs,
            source_logs = EXCLUDED.source_logs,
            report = EXCLUDED.report,
            artifact_root = EXCLUDED.artifact_root,
            storage_prefix = EXCLUDED.storage_prefix,
            screenshot_url = EXCLUDED.screenshot_url,
            main_image_url = EXCLUDED.main_image_url,
            artifact_bundle_url = EXCLUDED.artifact_bundle_url,
            artifact_manifest_url = EXCLUDED.artifact_manifest_url,
            storage_meta = EXCLUDED.storage_meta;
        """

        sql_artifacts = f"""
        INSERT INTO {self.ARTIFACTS_TABLE_NAME} (
            job_id, screenshot_bytes, site_json, manifest_json,
            artifact_bundle_bytes, storage_meta, created_at
        ) VALUES (
            %(job_id)s, %(screenshot_bytes)s, %(site_json)s::jsonb, %(manifest_json)s::jsonb,
            %(artifact_bundle_bytes)s, %(storage_meta)s::jsonb, %(created_at)s
        )
        ON CONFLICT (job_id) DO UPDATE SET
            screenshot_bytes = EXCLUDED.screenshot_bytes,
            site_json = EXCLUDED.site_json,
            manifest_json = EXCLUDED.manifest_json,
            artifact_bundle_bytes = EXCLUDED.artifact_bundle_bytes,
            storage_meta = EXCLUDED.storage_meta;
        """

        payload = dict(snapshot)
        payload["live_logs"] = _json_text(payload.get("live_logs") or [])
        payload["source_logs"] = _json_text(payload.get("source_logs") or [])
        payload["report"] = _json_text(payload.get("report"))
        payload["storage_meta"] = _json_text(payload.get("storage_meta") or {})
        payload["site_json"] = _json_text(payload.get("site_json"))
        payload["manifest_json"] = _json_text(payload.get("manifest_json"))

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_jobs, payload)
                cur.execute(sql_artifacts, payload)
            conn.commit()

    def load(self, job_id: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        sql = f"""
        SELECT j.*, a.screenshot_bytes, a.site_json, a.manifest_json, a.artifact_bundle_bytes, a.storage_meta as artifact_storage_meta
        FROM {self.TABLE_NAME} j
        LEFT JOIN {self.ARTIFACTS_TABLE_NAME} a ON j.job_id = a.job_id
        WHERE j.job_id = %s LIMIT 1
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (job_id,))
                row = cur.fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    def latest_completed(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        sql = f"""
        SELECT j.*, a.screenshot_bytes, a.site_json, a.manifest_json, a.artifact_bundle_bytes, a.storage_meta as artifact_storage_meta
        FROM {self.TABLE_NAME} j
        LEFT JOIN {self.ARTIFACTS_TABLE_NAME} a ON j.job_id = a.job_id
        WHERE j.report IS NOT NULL
        ORDER BY COALESCE(j.done_at, j.updated_at) DESC
        LIMIT 1
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
        if not row:
            return None
        return self._row_to_job(row)

    def _row_to_job(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": row.get("job_id"),
            "url": row.get("url"),
            "repo_url": row.get("repo_url"),
            "mode": row.get("mode"),
            "status": row.get("status"),
            "created_at": _as_datetime(row.get("created_at")),
            "updated_at": _as_datetime(row.get("updated_at")),
            "started_at": _as_datetime(row.get("started_at")),
            "done_at": _as_datetime(row.get("done_at")),
            "return_code": row.get("return_code"),
            "error": row.get("error"),
            "live_logs": _json_value(row.get("live_logs"), []),
            "source_logs": _json_value(row.get("source_logs"), []),
            "report": _json_value(row.get("report"), None),
            "artifact_root": row.get("artifact_root"),
            "storage_prefix": row.get("storage_prefix"),
            "screenshot_url": row.get("screenshot_url"),
            "main_image_url": row.get("main_image_url"),
            "artifact_bundle_url": row.get("artifact_bundle_url"),
            "artifact_manifest_url": row.get("artifact_manifest_url"),
            "storage_meta": _json_value(row.get("artifact_storage_meta") or row.get("storage_meta"), {}),
            "screenshot_bytes": bytes(row.get("screenshot_bytes")) if row.get("screenshot_bytes") is not None else None,
            "artifact_bundle_bytes": bytes(row.get("artifact_bundle_bytes")) if row.get("artifact_bundle_bytes") is not None else None,
            "site_json": _json_value(row.get("site_json"), None),
            "manifest_json": _json_value(row.get("manifest_json"), None),
        }


class HumanonnPersistence:
    def __init__(self, config: PersistenceConfig) -> None:
        self.config = config
        self.job_store = PostgresJobStore(config.database_url) if config.database_url else None
        self.storage = SupabaseStorageClient(config)

    @classmethod
    def from_env(cls) -> "HumanonnPersistence":
        return cls(PersistenceConfig.from_env())

    @property
    def enabled(self) -> bool:
        return bool(self.job_store and self.job_store.enabled)

    def ensure_schema(self) -> None:
        if self.job_store:
            self.job_store.ensure_schema()

    def snapshot_for_job(self, job: Any) -> dict[str, Any]:
        started_at = getattr(job, "created_at", None)
        now = _utc_now()
        report = getattr(job, "report", None)
        report = report if isinstance(report, dict) else None
        storage_meta = getattr(job, "storage_meta", None) or {}
        return {
            "job_id": getattr(job, "job_id", None),
            "url": getattr(job, "url", None),
            "repo_url": getattr(job, "repo_url", None),
            "mode": getattr(job, "mode", None),
            "status": self._status_for_job(job),
            "created_at": _as_datetime(started_at) or now,
            "updated_at": now,
            "started_at": _as_datetime(started_at) or now,
            "done_at": now if getattr(job, "done", False) else None,
            "return_code": getattr(job, "return_code", None),
            "error": getattr(job, "error", None),
            "live_logs": list(getattr(job, "live_logs", []) or []),
            "source_logs": list(getattr(job, "source_logs", []) or []),
            "report": report,
            "artifact_root": self._report_artifact_root(report),
            "storage_prefix": storage_meta.get("storage_prefix") or self._storage_prefix(getattr(job, "job_id", "")),
            "screenshot_url": storage_meta.get("screenshot_url"),
            "main_image_url": storage_meta.get("main_image_url"),
            "artifact_bundle_url": storage_meta.get("artifact_bundle_url"),
            "artifact_manifest_url": storage_meta.get("artifact_manifest_url"),
            "storage_meta": storage_meta,
            "screenshot_bytes": getattr(job, "screenshot_bytes", None),
            "artifact_bundle_bytes": getattr(job, "artifact_bundle_bytes", None),
            "site_json": report,
            "manifest_json": getattr(job, "manifest_json", None),
        }

    def upsert_job(self, job: Any) -> None:
        if self.job_store:
            self.job_store.upsert(self.snapshot_for_job(job))

    def load_job(self, job_id: str) -> dict[str, Any] | None:
        if not self.job_store:
            return None
        return self.job_store.load(job_id)

    def latest_completed_job(self) -> dict[str, Any] | None:
        if not self.job_store:
            return None
        return self.job_store.latest_completed()

    def sync_artifacts(self, job: Any) -> dict[str, Any] | None:
        import sys
        report = getattr(job, "report", None)
        if not isinstance(report, dict):
            return None

        existing_meta = getattr(job, "storage_meta", None) or report.get("storage_meta") or {}
        if existing_meta.get("stored_in_db"):
            return existing_meta

        # 1. Read screenshot as bytes if it exists
        screenshot_path = self._resolve_path(report.get("screenshot_path"))
        screenshot_bytes = None
        if screenshot_path and screenshot_path.exists():
            try:
                screenshot_bytes = screenshot_path.read_bytes()
                setattr(job, "screenshot_bytes", screenshot_bytes)
            except Exception as e:
                print(f"Failed to read screenshot file: {e}", file=sys.stderr)

        # 2. Read manifest.json as dict if it exists
        raw = report.get("raw") or {}
        manifest_path = self._resolve_path(raw.get("manifest_path"))
        manifest_json = None
        if manifest_path and manifest_path.exists():
            try:
                import json
                manifest_json = json.loads(manifest_path.read_text(encoding="utf-8"))
                setattr(job, "manifest_json", manifest_json)
            except Exception as e:
                print(f"Failed to read manifest file: {e}", file=sys.stderr)

        # 3. Bundle artifacts into zip bytes if the root directory exists
        artifact_root = self._report_artifact_root(report)
        artifact_bundle_bytes = None
        if artifact_root:
            artifact_root_path = Path(artifact_root)
            if artifact_root_path.exists():
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
                    temp_zip = Path(handle.name)
                try:
                    with zipfile.ZipFile(temp_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for file_path in sorted(artifact_root_path.rglob("*")):
                            if file_path.is_file():
                                archive.write(file_path, arcname=str(file_path.relative_to(artifact_root_path)))
                    zip_size = temp_zip.stat().st_size
                    if zip_size < 25 * 1024 * 1024:
                        artifact_bundle_bytes = temp_zip.read_bytes()
                        setattr(job, "artifact_bundle_bytes", artifact_bundle_bytes)
                    else:
                        print(f"Artifact bundle size ({zip_size / 1024 / 1024:.2f}MB) exceeds 25MB limit. Skipping DB storage to prevent OOM.", file=sys.stderr)
                except Exception as e:
                    print(f"Failed to create artifact bundle: {e}", file=sys.stderr)
                finally:
                    try:
                        temp_zip.unlink(missing_ok=True)
                    except Exception:
                        pass

        # 4. Create storage metadata
        meta: dict[str, Any] = {
            "stored_in_db": True,
            "has_screenshot": screenshot_bytes is not None,
            "has_bundle": artifact_bundle_bytes is not None,
            "has_manifest": manifest_json is not None,
        }

        setattr(job, "storage_meta", meta)
        if isinstance(job.report, dict):
            job.report["storage_meta"] = meta

        import gc
        gc.collect()
        return meta

    def _storage_prefix(self, job_id: str) -> str:
        job_part = job_id or "scan"
        return f"{self.config.storage_prefix}/{job_part}"

    def _report_artifact_root(self, report: dict[str, Any] | None) -> str | None:
        if not report:
            return None
        raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
        if isinstance(raw, dict):
            artifact_root = raw.get("artifact_root")
            if artifact_root:
                return str(artifact_root)
        return None

    def _resolve_path(self, raw_path: Any) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(str(raw_path))
        if candidate.exists():
            return candidate
        return None

    def _find_local_report_path(self, job: Any, report: dict[str, Any]) -> Path | None:
        output_path = getattr(job, "output_path", None)
        if isinstance(output_path, Path) and output_path.exists():
            return output_path
        raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
        if isinstance(raw, dict):
            manifest_path = raw.get("manifest_path")
            if manifest_path:
                candidate = Path(str(manifest_path)).with_name("site.json")
                if candidate.exists():
                    return candidate
        return None

    def _status_for_job(self, job: Any) -> str:
        if getattr(job, "done", False):
            if getattr(job, "error", None):
                return "failed" if getattr(job, "return_code", None) not in (0, None) else "done"
            return "done"
        return "running"
