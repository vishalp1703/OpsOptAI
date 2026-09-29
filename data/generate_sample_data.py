"""
generate_sample_data.py
========================
Dev/testing utility — NOT part of the production ingestion pipeline.

Generates realistic synthetic raw data for all 8 OpsPilot AI sources
and writes them to data/raw/. Data is intentionally messy in places
(missing values, inconsistent date formats, stray whitespace, a few
duplicate IDs) so the Phase 1 cleaning logic actually gets exercised
and tested, not just run against pristine input.

Run:
    python data/generate_sample_data.py
    python data/generate_sample_data.py --rows 100 --seed 7
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pandas as pd
from faker import Faker

fake = Faker()

STORES = ["Store-101 Downtown", "Store-102 Uptown", "Store-103 Eastside",
          "Store-104 Westgate", "Store-105 Mall Plaza"]

DEPARTMENTS_FLAVOR = ["checkout", "returns", "curbside pickup", "customer service desk",
                       "inventory", "delivery", "cleanliness", "staffing"]

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d, %Y", "%m/%d/%y"]


def messy_date() -> str:
    """Random recent date, formatted inconsistently on purpose."""
    d = fake.date_between(start_date="-120d", end_date="today")
    fmt = random.choice(DATE_FORMATS)
    return d.strftime(fmt)


def maybe_blank(value: str, blank_rate: float = 0.05) -> str:
    """Randomly blank out a value to simulate real-world missing data."""
    return "" if random.random() < blank_rate else value


def sprinkle_whitespace(text: str) -> str:
    """Randomly add stray leading/trailing/internal whitespace."""
    if random.random() < 0.15:
        text = "  " + text + "   "
    if random.random() < 0.1:
        text = text.replace(" ", "  ", 1)
    return text


def gen_customer_reviews(n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        complaint = random.choice(DEPARTMENTS_FLAVOR)
        rows.append({
            "review_id": f"RV{i:04d}",
            "store_location": maybe_blank(random.choice(STORES)),
            "review_date": messy_date(),
            "rating": random.choice([1, 1, 2, 2, 3, 4, 5]),
            "review_text": sprinkle_whitespace(
                f"The {complaint} experience was frustrating — {fake.sentence(nb_words=10)}"
            ),
            "platform": random.choice(["Google", "Yelp", "Facebook"]),
        })
    # inject a couple of duplicate IDs to test dedup
    if n >= 5:
        rows.append(dict(rows[2]))
    return pd.DataFrame(rows)


def gen_support_tickets(n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "ticket_id": f"TCK{i:04d}",
            "store": maybe_blank(random.choice(STORES)),
            "created_date": messy_date(),
            "priority": random.choice(["Low", "Medium", "High", "Urgent"]),
            "subject": sprinkle_whitespace(f"Issue with {random.choice(DEPARTMENTS_FLAVOR)}"),
            "description": fake.paragraph(nb_sentences=2),
            "status": random.choice(["Open", "Closed", "Pending"]),
        })
    return pd.DataFrame(rows)


def gen_pos_logs(n: int) -> pd.DataFrame:
    rows = []
    exc_types = ["VOID", "REFUND", "DISCOUNT_OVERRIDE", "PRICE_CORRECTION"]
    for i in range(1, n + 1):
        rows.append({
            "log_id": f"POS{i:04d}",
            "store_id": maybe_blank(random.choice(STORES)),
            "transaction_date": messy_date(),
            "exception_type": random.choice(exc_types),
            "amount": round(random.uniform(2, 150), 2),
            "cashier_id": f"EMP{random.randint(1, 40):03d}",
            "notes": sprinkle_whitespace(
                random.choice([
                    "Register froze mid-transaction, had to reissue",
                    "Customer disputed price, matched competitor",
                    "Item scanned wrong price, manual override applied",
                    "System timeout during payment, refunded and retried",
                    "",
                ])
            ),
        })
    return pd.DataFrame(rows)


def gen_employee_feedback(n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "feedback_id": f"EMP{i:04d}",
            "store": maybe_blank(random.choice(STORES)),
            "submission_date": messy_date(),
            "employee_role": random.choice(["Cashier", "Shift Lead", "Stock Associate", "Manager"]),
            "feedback_text": sprinkle_whitespace(
                f"We're consistently short-staffed during {random.choice(['weekend', 'evening', 'holiday'])} shifts — {fake.sentence(nb_words=8)}"
            ),
        })
    return pd.DataFrame(rows)


def gen_surveys(n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "survey_id": f"SUR{i:04d}",
            "store": maybe_blank(random.choice(STORES)),
            "response_date": messy_date(),
            "nps_score": random.randint(0, 10),
            "comments": sprinkle_whitespace(fake.sentence(nb_words=12)),
        })
    return pd.DataFrame(rows)


def gen_social_media(n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "post_id": f"SOC{i:04d}",
            "store_mentioned": maybe_blank(random.choice(STORES)),
            "post_date": messy_date(),
            "platform": random.choice(["Twitter/X", "Instagram", "TikTok"]),
            "text": sprinkle_whitespace(
                f"Never going back to this store, {fake.sentence(nb_words=10)}"
            ),
            "likes": random.randint(0, 500),
        })
    return pd.DataFrame(rows)


def gen_crm_notes(n: int) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "note_id": f"CRM{i:04d}",
            "account": maybe_blank(random.choice(STORES)),
            "note_date": messy_date(),
            "rep_name": fake.name(),
            "note_text": sprinkle_whitespace(fake.paragraph(nb_sentences=1)),
        })
    return pd.DataFrame(rows)


def gen_incident_reports(n: int) -> pd.DataFrame:
    rows = []
    categories = ["Safety", "Equipment Failure", "Theft", "Staffing Shortage", "Facility"]
    for i in range(1, n + 1):
        rows.append({
            "incident_id": f"INC{i:04d}",
            "store": maybe_blank(random.choice(STORES)),
            "incident_date": messy_date(),
            "severity": random.choice(["Low", "Medium", "High", "Critical"]),
            "category": random.choice(categories),
            "description": fake.paragraph(nb_sentences=2),
        })
    return pd.DataFrame(rows)


GENERATORS = {
    "customer_reviews": gen_customer_reviews,
    "support_tickets": gen_support_tickets,
    "pos_logs": gen_pos_logs,
    "employee_feedback": gen_employee_feedback,
    "surveys": gen_surveys,
    "social_media": gen_social_media,
    "crm_notes": gen_crm_notes,
    "incident_reports": gen_incident_reports,
}


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic OpsPilot AI sample data")
    parser.add_argument("--rows", type=int, default=40, help="Rows per source (default: 40)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--out-dir", type=str, default="data/raw", help="Output directory")
    args = parser.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for source_key, gen_fn in GENERATORS.items():
        df = gen_fn(args.rows)
        out_path = out_dir / f"{source_key}.csv"
        df.to_csv(out_path, index=False)
        print(f"✅ {source_key:<20} {len(df):>4} rows -> {out_path}")

    print(f"\nDone. Generated {len(GENERATORS)} raw source files with ~{args.rows} rows each in {out_dir}/")


if __name__ == "__main__":
    main()
