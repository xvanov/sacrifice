from celery import shared_task


@shared_task(name="pushup_counter.run_pushup_counter_verification_task")
def run_pushup_counter_verification_task(
    goal_id_str: str,
    submission_id_str: str,
    proof_data: dict,
    criteria_data: dict,
):
    """Celery task stub — real impl runs verification asynchronously."""
    pass
