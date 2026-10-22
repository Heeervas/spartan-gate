# Hermes thread capacity — diagnostic guidance

Status: pending operational recommendation, generalized from an earlier local
investigation. This note does not change resource limits or assert current health.

When tool processes fail with `cannot start new thread`, compare cgroup PID/thread
usage and limit-hit counters with application logs. Coincident exhaustion supports
an immediate capacity problem but does not establish a leak or hardware cause.

Before changing a limit, check memory pressure, process ownership and abnormal
thread growth. If a bounded adjustment is explicitly authorized, preserve other
security controls, verify service continuity and exercise representative tools.
Monitor new limit hits under normal parallel load: historical counters may be
cumulative and should not be mistaken for fresh failures. Do not remove ceilings
or increase them repeatedly instead of investigating sustained growth.
