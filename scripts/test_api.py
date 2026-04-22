"""
Test script for the Verisent API using an org API key.
Fetches submissions and prints the submitted data.
"""

import httpx

BASE_URL = "http://localhost:8000"
API_KEY = ""

HEADERS = {"x-api-key": API_KEY}


def main():
    client = httpx.Client(base_url=BASE_URL, headers=HEADERS)

    print("=== Listing submissions ===")
    resp = client.get("/v1/api/submissions")
    print(f"Status: {resp.status_code}")

    if resp.status_code != 200:
        print(f"Error: {resp.text}")
        return

    submissions = resp.json()["submissions"]
    print(f"Found {len(submissions)} submissions\n")

    if not submissions:
        print("No submissions.")
        return

    print("=== Submissions ===")
    for sub in submissions:
        print(f"\nSubmission {sub['submission_id']}:")
        print(f"  Form: {sub['form_name']}")
        print(f"  User: {sub['email']}")
        print(f"  Submitted: {sub['completed_at']}")

        data_url = sub.get("data_url")
        if not data_url:
            print("  Status: Pending (no data yet)")
            continue

        blob_resp = httpx.get(data_url)
        if blob_resp.status_code != 200:
            print(f"  Error downloading blob: {blob_resp.status_code}")
            continue

        print(f"  Data: {blob_resp.text}")


if __name__ == "__main__":
    main()
