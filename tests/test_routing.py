from paperlens.routing import is_multi_aspect_comparison, is_multi_paper_question


def test_multi_aspect_comparison_is_detected_without_routing_a_tool() -> None:
    question = (
        "چهار مقاله را از نظر ایدهٔ اصلی، تغییر معماری، مزایا، محدودیت‌ها و "
        "مسیر تکامل روش‌ها با یکدیگر مقایسه کن."
    )

    assert is_multi_paper_question(question)
    assert is_multi_aspect_comparison(question)


def test_single_focus_question_is_not_multi_aspect() -> None:
    assert not is_multi_aspect_comparison("محدودیت‌های چهار مقاله را مقایسه کن")
