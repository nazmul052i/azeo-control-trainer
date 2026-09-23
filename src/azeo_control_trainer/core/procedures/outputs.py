"""Confirmation and checked application for governed procedure outputs."""


def confirm_tag_output(engine, run_id, step, operation):
    if not step.require_confirmation:
        return
    prompt = step.display_text or step.operator_guidance or step.description or operation
    message_id = engine._create_operator_message(
        run_id,
        step,
        "OUTPUT_CONFIRMATION",
        prompt,
        data={"operation": operation, "tag": engine._audit_tag(step.tag or "")},
    )
    if engine.auto_confirm:
        confirmed = True
    elif engine.confirm_fn:
        confirmed = engine.confirm_fn(step, prompt)
    else:
        response = input(f"CONFIRM OUTPUT {step.id}: {prompt} [y/N]: ").strip().lower()
        confirmed = response in {"y", "yes"}
    engine.store.log_event(
        run_id,
        "PCS_OUTPUT_CONFIRMATION",
        engine._prefixed(step.id),
        "Output confirmed" if confirmed else "Output declined",
        {"confirmed": confirmed, "auto_confirm": engine.auto_confirm,
         "operation": operation, "tag": engine._audit_tag(step.tag or "")},
    )
    if not confirmed:
        raise RuntimeError(f"Operator declined output for step {step.id}")
    engine._acknowledge_operator_message(message_id, comment=operation)


def authorize_tag_output(engine, run_id, step, operation, *, schedule=None):
    """Authorize one live output or a complete bounded ramp schedule.

    A live output always requires authorization. Trial/proposal engines retain
    the authored ``require_confirmation`` behavior.
    """
    callback = getattr(engine, "output_authorize_fn", None)
    if not callable(callback):
        confirm_tag_output(engine, run_id, step, operation)
        return False
    prompt = step.display_text or step.description or operation
    payload = {
        "operation": operation,
        "logical_tag": step.tag,
        "path": engine.output_bindings[step.tag],
        "value": step.value,
        "schedule": schedule,
    }
    message_id = engine._create_operator_message(
        run_id,
        step,
        "OUTPUT_AUTHORIZATION",
        prompt,
        data=payload,
    )
    confirmed = bool(callback(step, prompt, payload))
    engine.store.log_event(
        run_id,
        "PCS_OUTPUT_CONFIRMATION",
        engine._prefixed(step.id),
        "Output authorized" if confirmed else "Output declined",
        {"confirmed": confirmed, "auto_confirm": False, **payload},
    )
    if not confirmed:
        raise RuntimeError(f"Operator declined output for step {step.id}")
    engine._acknowledge_operator_message(message_id, comment=operation)
    return True


def apply_checked_output(engine, run_id, step, value):
    logical_tag = step.tag or ""
    try:
        path = engine.output_bindings[logical_tag]
    except KeyError as error:
        raise RuntimeError(f"No checked output binding for {logical_tag}") from error
    if not callable(getattr(engine, "output_apply_fn", None)):
        raise RuntimeError("The host checked-output service is unavailable")
    result = engine.output_apply_fn(step, path, value)
    success = bool(getattr(result, "success", result.get("success", False) if isinstance(result, dict) else result))
    detail = str(getattr(result, "error", result.get("error", "") if isinstance(result, dict) else ""))
    engine.store.log_event(
        run_id,
        "PCS_OUTPUT_APPLIED" if success else "PCS_OUTPUT_REJECTED",
        engine._prefixed(step.id),
        f"Checked output {'applied' if success else 'rejected'}: {path}",
        {"logical_tag": logical_tag, "path": path, "value": value, "success": success, "error": detail},
    )
    if not success:
        raise RuntimeError(f"Checked output rejected for {path}: {detail or 'unspecified reason'}")
    engine.store.log_write(run_id, path, value)


__all__ = ["apply_checked_output", "authorize_tag_output", "confirm_tag_output"]
