import csv


def read_list_from_file(file_path: str) -> list[str]:
    """Read lines from a file into a clean list."""
    with open(file_path) as f:
        return [line.strip() for line in f if line.strip()]


def export_to_csv(file_path: str, data: list[tuple], headers: list[str]):
    """Export table data to CSV."""
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(data)
