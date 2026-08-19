from agents.crews.mas_crew import run_mas_review
from agents.crews.single_crew import run_single_review


def is_complex(code: str):

    keywords = [
        "sql",
        "thread",
        "async",
        "lock",
        "auth",
        "password",
        "database"
    ]

    return any(k in code.lower() for k in keywords)


def review_code(code: str):

    if is_complex(code):

        print("Using MAS Review")

        return run_mas_review(code)

    else:

        print("Using Single Review")

        return run_single_review(code)
