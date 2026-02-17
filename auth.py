import hashlib
import base64
import psycopg2
import psycopg2.extras

def connect(config):
    return psycopg2.connect(
        host=config.get("db_host", "localhost"),
        port=config.get("db_port", 5432),
        dbname=config.get("db_name", "django_db"),
        user=config.get("db_user", "django_user"),
        password=config.get("db_password", "2137"),
    )

def _verify_django_password(plain_password, encoded):
    try:
        algorithm, iterations, salt, hash_b64 = encoded.split("$", 3)
        computed = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode(), salt.encode(), int(iterations),
        )
        return base64.b64encode(computed).decode() == hash_b64
    except Exception:
        return False

def authenticate(cur, email, password):
    cur.execute(
        "SELECT id, password FROM backend_main_user_object WHERE email = %s AND is_active = true",
        (email,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"User not found: {email}")
    if not _verify_django_password(password, row["password"]):
        raise RuntimeError("Invalid password")
    return row["id"]
