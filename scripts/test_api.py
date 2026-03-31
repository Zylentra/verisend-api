"""
Test script for the Verisend API using an org API key.
Fetches submissions and decrypts them.
"""

import base64
import hashlib
import struct
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

BASE_URL = "http://localhost:8000"
API_KEY = ""
PASSWORD = ""

HEADERS = {"x-api-key": API_KEY}


def decrypt_private_key_with_passphrase(encrypted_b64: str, passphrase: str) -> bytes:
    """Decrypt a private key that was encrypted with PBKDF2 + AES-GCM.
    Format: [16-byte salt][12-byte IV][AES-GCM ciphertext]
    """
    raw = base64.b64decode(encrypted_b64)
    salt = raw[:16]
    iv = raw[16:28]
    ciphertext = raw[28:]

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    key = kdf.derive(passphrase.encode())
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ciphertext, None)


def hybrid_decrypt(encrypted_data: bytes, private_key_bytes: bytes) -> bytes:
    """Decrypt hybrid-encrypted data.
    Format: [2-byte key length][RSA-encrypted AES key][12-byte IV][AES-GCM ciphertext]
    """
    private_key = serialization.load_der_private_key(private_key_bytes, password=None)

    key_len = struct.unpack(">H", encrypted_data[:2])[0]
    encrypted_aes_key = encrypted_data[2:2 + key_len]
    iv = encrypted_data[2 + key_len:2 + key_len + 12]
    ciphertext = encrypted_data[2 + key_len + 12:]

    aes_key = private_key.decrypt(
        encrypted_aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    aesgcm = AESGCM(aes_key)
    return aesgcm.decrypt(iv, ciphertext, None)


def main():
    client = httpx.Client(base_url=BASE_URL, headers=HEADERS)

    # 1. List submissions
    print("=== Listing submissions ===")
    resp = client.get("/v1/api/submissions")
    print(f"Status: {resp.status_code}")

    if resp.status_code != 200:
        print(f"Error: {resp.text}")
        return

    data = resp.json()
    submissions = data["submissions"]
    print(f"Found {len(submissions)} submissions\n")

    if not submissions:
        print("No submissions to decrypt.")
        return

    # 2. Get decryption keys from response
    encrypted_private_key = data["encrypted_private_key"]
    encrypted_org_private_key = data["encrypted_org_private_key"]

    print("=== Decrypting keys ===")

    # Step 1: Decrypt API key's private key with password
    api_private_key_bytes = decrypt_private_key_with_passphrase(encrypted_private_key, PASSWORD)
    print("API key private key decrypted")

    # Step 2: Decrypt org private key with API key's private key
    org_private_key_bytes = hybrid_decrypt(
        base64.b64decode(encrypted_org_private_key),
        api_private_key_bytes,
    )
    print("Org private key decrypted")

    # 3. Decrypt each submission
    print("\n=== Decrypting submissions ===")
    for sub in submissions:
        print(f"\nSubmission {sub['submission_id']}:")
        print(f"  Form: {sub['form_name']}")
        print(f"  User: {sub['email']}")
        print(f"  Submitted: {sub['completed_at']}")

        data_url = sub.get("data_url")
        if not data_url:
            print("  Status: Pending (no data yet)")
            continue

        # Download encrypted blob
        blob_resp = httpx.get(data_url)
        if blob_resp.status_code != 200:
            print(f"  Error downloading blob: {blob_resp.status_code}")
            continue

        # Decrypt submission
        try:
            plaintext = hybrid_decrypt(blob_resp.content, org_private_key_bytes)
            print(f"  Decrypted data: {plaintext.decode()}")
        except Exception as e:
            print(f"  Decryption failed: {e}")


if __name__ == "__main__":
    main()
