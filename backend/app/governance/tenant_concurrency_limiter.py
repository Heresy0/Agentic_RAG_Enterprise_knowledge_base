from dataclasses import dataclass
from contextlib import contextmanager
from threading import Event, Thread
from uuid import UUID, uuid4

from redis import Redis
from redis.exceptions import RedisError


ACQUIRE_SCRIPT = """
local limit = tonumber(ARGV[1])
local lease_ms = tonumber(ARGV[2])
local token = ARGV[3]

local redis_time = redis.call("TIME")
local now_ms = (
    tonumber(redis_time[1]) * 1000
    + math.floor(tonumber(redis_time[2]) / 1000)
)

redis.call(
    "ZREMRANGEBYSCORE",
    KEYS[1],
    "-inf",
    now_ms
)

local active = tonumber(
    redis.call("ZCARD", KEYS[1])
)

if active >= limit then
    local oldest = redis.call(
        "ZRANGE",
        KEYS[1],
        0,
        0,
        "WITHSCORES"
    )

    local retry_after = 1

    if #oldest >= 2 then
        retry_after = math.max(
            1,
            math.ceil(
                (
                    tonumber(oldest[2])
                    - now_ms
                ) / 1000
            )
        )
    end

    redis.call("PEXPIRE", KEYS[1], math.max(lease_ms, redis.call("PTTL", KEYS[1])))

    return {
        0,
        active,
        0,
        retry_after
    }
end

local expires_at_ms = now_ms + lease_ms

redis.call(
    "ZADD",
    KEYS[1],
    expires_at_ms,
    token
)

active = active + 1

redis.call("PEXPIRE", KEYS[1], math.max(lease_ms, redis.call("PTTL", KEYS[1])))

return {
    1,
    active,
    limit - active,
    0
}
"""


RELEASE_SCRIPT = """
local removed = redis.call(
    "ZREM",
    KEYS[1],
    ARGV[1]
)

if redis.call("ZCARD", KEYS[1]) == 0 then
    redis.call("DEL", KEYS[1])
end

return removed
"""

RENEW_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not score or tonumber(score) <= now then return 0 end
local lease_ms = tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], now + lease_ms, ARGV[1])
redis.call('PEXPIRE', KEYS[1], math.max(lease_ms, redis.call('PTTL', KEYS[1])))
return 1
"""


@dataclass(frozen=True, slots=True)
class ConcurrencyLease:
    tenant_id: UUID
    resource: str
    token: str


@dataclass(frozen=True, slots=True)
class ConcurrencyDecision:
    allowed: bool
    limit: int
    active: int
    remaining: int
    retry_after_seconds: int
    lease: ConcurrencyLease | None


class TenantConcurrencyExceeded(Exception):
    def __init__(
        self,
        decision: ConcurrencyDecision,
    ) -> None:
        super().__init__("租户并发请求超过限制。")
        self.decision = decision


class ConcurrencyLimiterUnavailable(RuntimeError):
    """Redis 无法完成并发槽位操作。"""


class TenantConcurrencyLimiter:
    def __init__(
        self,
        redis_client: Redis,
        *,
        key_prefix: str = "enterprise-kb",
    ) -> None:
        self.redis_client = redis_client
        self.key_prefix = key_prefix
        self.acquire_script = (
            redis_client.register_script(
                ACQUIRE_SCRIPT
            )
        )
        self.release_script = (
            redis_client.register_script(
                RELEASE_SCRIPT
            )
        )
        self.renew_script = redis_client.register_script(RENEW_SCRIPT)

    def _key(
        self,
        *,
        tenant_id: UUID,
        resource: str,
    ) -> str:
        if not resource or not all(
            character.isalnum()
            or character in "-_:"
            for character in resource
        ):
            raise ValueError("resource 格式无效")

        return (
            f"{self.key_prefix}:concurrency:"
            f"{{{tenant_id}}}:{resource}"
        )

    def acquire(
        self,
        *,
        tenant_id: UUID,
        resource: str,
        limit: int,
        lease_seconds: int,
    ) -> ConcurrencyDecision:
        if limit < 1:
            raise ValueError("limit 必须是正整数")

        if lease_seconds < 1:
            raise ValueError(
                "lease_seconds 必须是正整数"
            )

        key = self._key(
            tenant_id=tenant_id,
            resource=resource,
        )
        token = uuid4().hex

        try:
            result = self.acquire_script(
                keys=[key],
                args=[
                    limit,
                    lease_seconds * 1000,
                    token,
                ],
            )

            allowed = bool(int(result[0]))
            active = int(result[1])
            remaining = int(result[2])
            retry_after_seconds = int(result[3])
        except RedisError as exc:
            raise ConcurrencyLimiterUnavailable(
                "Redis 并发限制服务不可用。"
            ) from exc
        except (
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            raise ConcurrencyLimiterUnavailable(
                "Redis 返回了无效的并发限制结果。"
            ) from exc

        lease = (
            ConcurrencyLease(
                tenant_id=tenant_id,
                resource=resource,
                token=token,
            )
            if allowed
            else None
        )

        return ConcurrencyDecision(
            allowed=allowed,
            limit=limit,
            active=max(active, 0),
            remaining=max(remaining, 0),
            retry_after_seconds=max(
                retry_after_seconds,
                0,
            ),
            lease=lease,
        )

    def release(
        self,
        lease: ConcurrencyLease,
    ) -> bool:
        key = self._key(
            tenant_id=lease.tenant_id,
            resource=lease.resource,
        )

        try:
            removed = self.release_script(
                keys=[key],
                args=[lease.token],
            )
            return bool(int(removed))
        except RedisError as exc:
            raise ConcurrencyLimiterUnavailable(
                "Redis 并发槽位释放失败。"
            ) from exc
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ConcurrencyLimiterUnavailable(
                "Redis 返回了无效的槽位释放结果。"
            ) from exc

    def renew(self, lease: ConcurrencyLease, *, lease_seconds: int) -> bool:
        """Renew only an existing unexpired token; never recreate a lost lease."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds 必须是正整数")
        try:
            return bool(int(self.renew_script(keys=[self._key(tenant_id=lease.tenant_id, resource=lease.resource)],
                                   args=[lease.token, lease_seconds * 1000])))
        except (RedisError, ValueError, TypeError) as exc:
            raise ConcurrencyLimiterUnavailable("Redis 并发槽位续期失败。") from exc


@contextmanager
def maintain_concurrency_lease(limiter, lease, *, lease_seconds, interval_seconds=None):
    """Keep a slow synchronous request counted, and reject success after lease loss."""
    stop, lost = Event(), Event()

    def check():
        if lost.is_set() or not limiter.renew(lease, lease_seconds=lease_seconds):
            lost.set()
            raise ConcurrencyLimiterUnavailable("问答并发租约已失效，请稍后重试。")

    check()
    interval = min(30, lease_seconds / 3) if interval_seconds is None else interval_seconds
    if interval <= 0:
        raise ValueError("interval_seconds 必须为正数")

    def heartbeat():
        while not stop.wait(interval):
            try:
                check()
            except Exception:
                lost.set()
                return

    thread = Thread(target=heartbeat, name="qa-concurrency-lease", daemon=True)
    thread.start()
    try:
        yield check
    finally:
        stop.set()
        thread.join(timeout=5)
