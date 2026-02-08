"""
Configuration from YAML file with Pydantic validation.
Hot reload on SIGHUP: load_config(path) replaces current config.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, field_validator

from proxy.upstream_pool import UpstreamPool, Upstream
from proxy.timeouts import TimeoutPolicy
from proxy.limits import ConnectionLimitManager, ConnectionLimits


logger = logging.getLogger(__name__)


def _parse_listen(value: str) -> Tuple[str, int]:
    """Parse 'host:port' into (host, port)."""
    value = (value or "").strip()
    if ":" in value:
        host, port_str = value.rsplit(":", 1)
        return host.strip() or "127.0.0.1", int(port_str)
    return "127.0.0.1", int(value) if value.isdigit() else 8080


# --- Pydantic models (YAML schema) ---


class UpstreamItem(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9001


class TimeoutsConfig(BaseModel):
    connect_ms: int = 1000
    read_ms: int = 15000
    write_ms: int = 15000
    total_ms: int = 30000


class LimitsConfig(BaseModel):
    max_client_conns: int = 1000
    max_conns_per_upstream: int = 100


class LoggingConfig(BaseModel):
    level: str = "info"

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, v: str) -> str:
        return (v or "info").strip().lower()


class ConfigModel(BaseModel):
    """Root config model (what we read from YAML)."""
    listen: str = "127.0.0.1:8080"
    metrics_listen: Optional[str] = "127.0.0.1:8081"
    upstreams: List[UpstreamItem] = []
    timeouts: TimeoutsConfig = TimeoutsConfig()
    limits: LimitsConfig = LimitsConfig()
    logging: LoggingConfig = LoggingConfig()

    @property
    def listen_host(self) -> str:
        return _parse_listen(self.listen)[0]

    @property
    def listen_port(self) -> int:
        return _parse_listen(self.listen)[1]

    @property
    def metrics_host(self) -> str:
        if self.metrics_listen is None:
            return "127.0.0.1"
        return _parse_listen(self.metrics_listen)[0]

    @property
    def metrics_port(self) -> int:
        if self.metrics_listen is None:
            return 8081
        return _parse_listen(self.metrics_listen)[1]


class ConfigHolder:
    """
    Holds validated config and derived objects (UpstreamPool, TimeoutPolicy, ConnectionLimitManager).
    Replaced atomically on reload.
    """
    __slots__ = ("model", "upstream_pool", "timeout_policy", "connection_limits")

    def __init__(self, model: ConfigModel):
        self.model = model
        upstreams = [Upstream(host=u.host, port=u.port) for u in model.upstreams]
        self.upstream_pool = UpstreamPool(upstreams) if upstreams else UpstreamPool([
            Upstream(host="127.0.0.1", port=9001),
            Upstream(host="127.0.0.1", port=9002),
        ])
        self.timeout_policy = TimeoutPolicy(
            connect_ms=model.timeouts.connect_ms,
            read_ms=model.timeouts.read_ms,
            write_ms=model.timeouts.write_ms,
            total_ms=model.timeouts.total_ms,
        )
        self.connection_limits = ConnectionLimitManager(ConnectionLimits(
            max_client_conns=model.limits.max_client_conns,
            max_conns_per_upstream=model.limits.max_conns_per_upstream,
        ))


# Current config (atomic reference); None = use env fallback until first load
_current: Optional[ConfigHolder] = None


def load_config(path: Union[str, Path]) -> Optional[ConfigHolder]:
    """
    Load config from YAML file, validate, build holder. On success replace current config.
    Returns the new holder or None on error (keeps previous config if any).
    """
    global _current
    path = Path(path)
    if not path.is_file():
        logger.warning("Config file not found: %s", path)
        return _current
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not data:
            data = {}
        model = ConfigModel.model_validate(data)
        holder = ConfigHolder(model)
        _current = holder
        logger.info("Config loaded from %s (listen=%s, upstreams=%d)", path, model.listen, len(model.upstreams))
        return holder
    except Exception as e:
        logger.error("Failed to load config from %s: %s", path, e, exc_info=True)
        return _current


def get_config() -> Optional[ConfigHolder]:
    """Return current config holder (None if never loaded and no file used)."""
    return _current


def set_config_fallback(holder: ConfigHolder) -> None:
    """Set current config (e.g. from env defaults when no file)."""
    global _current
    _current = holder


def build_fallback_from_env() -> ConfigHolder:
    """Build ConfigHolder from environment (same semantics as before config file)."""
    import os
    listen = f"{os.environ.get('PROXY_LISTEN_HOST', '127.0.0.1')}:{os.environ.get('PROXY_LISTEN_PORT', '8080')}"
    metrics = f"{os.environ.get('METRICS_LISTEN_HOST', '127.0.0.1')}:{os.environ.get('METRICS_LISTEN_PORT', '8081')}"
    hosts_str = os.environ.get("UPSTREAM_HOSTS", "").strip()
    upstreams: List[UpstreamItem] = []
    if hosts_str:
        for part in hosts_str.split(","):
            part = part.strip()
            if ":" in part:
                host, port_str = part.rsplit(":", 1)
                try:
                    upstreams.append(UpstreamItem(host=host.strip(), port=int(port_str)))
                except ValueError:
                    pass
    if not upstreams:
        upstreams = [
            UpstreamItem(host="127.0.0.1", port=9001),
            UpstreamItem(host="127.0.0.1", port=9002),
        ]
    model = ConfigModel(
        listen=listen,
        metrics_listen=metrics,
        upstreams=upstreams,
        timeouts=TimeoutsConfig(),
        limits=LimitsConfig(max_client_conns=500, max_conns_per_upstream=100),
        logging=LoggingConfig(level=os.environ.get("LOG_LEVEL", "info")),
    )
    return ConfigHolder(model)
