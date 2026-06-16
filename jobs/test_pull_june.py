from jobs.meta_pull import run_meta_pull

result = run_meta_pull(
    from_date="2026-06-01",
    to_date="2026-06-02"
)

print(result)
