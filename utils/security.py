import os
from datetime import datetime, timedelta, timezone
import jwt
import bcrypt
import hashlib
from dotenv import load_dotenv
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

load_dotenv()

# Configuration
ALGORITHM = "RS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15       # 15 minutes
REFRESH_TOKEN_EXPIRE_DAYS = 7          # 7 days

# Key Management
KEYS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "keys")
PRIVATE_KEY_PATH = os.path.join(KEYS_DIR, "private_key.pem")
PUBLIC_KEY_PATH = os.path.join(KEYS_DIR, "public_key.pem")

def generate_keys():
    """Generate RSA keys if they do not exist."""
    if not os.path.exists(KEYS_DIR):
        os.makedirs(KEYS_DIR)
        
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    public_key = private_key.public_key()

    with open(PRIVATE_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    with open(PUBLIC_KEY_PATH, "wb") as f:
        f.write(public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ))

if not os.path.exists(PRIVATE_KEY_PATH) or not os.path.exists(PUBLIC_KEY_PATH):
    generate_keys()

with open(PRIVATE_KEY_PATH, "r") as f:
    PRIVATE_KEY = f.read()

with open(PUBLIC_KEY_PATH, "r") as f:
    PUBLIC_KEY = f.read()

def _pre_hash(password: str) -> str:
    """Pre-hash with SHA-256 to avoid bcrypt's 72-byte limit."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_pre_hash(password).encode('utf-8'), bcrypt.gensalt())
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    hashed_pwd_bytes = hashed_password.encode('utf-8')
    try:
        # Try backwards compatibility for short passwords that were hashed directly
        if len(plain_password.encode("utf-8")) <= 72:
            if bcrypt.checkpw(plain_password.encode('utf-8'), hashed_pwd_bytes):
                return True
    except Exception:
        pass
    
    # Try with the pre-hashed version
    try:
        return bcrypt.checkpw(_pre_hash(plain_password).encode('utf-8'), hashed_pwd_bytes)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> tuple[str, str]:
    """Returns (token, token_jti). JTI is useful if we need to blacklist it later."""
    import uuid
    to_encode = data.copy()
    
    jti = str(uuid.uuid4())
    to_encode["jti"] = jti
    to_encode["type"] = "access"

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, PRIVATE_KEY, algorithm=ALGORITHM)
    return encoded_jwt, jti

def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> tuple[str, str, datetime]:
    """Returns (token, token_jti, expiry)."""
    import uuid
    to_encode = {"sub": data.get("sub")}  # only user id in refresh token
    
    jti = str(uuid.uuid4())
    to_encode["jti"] = jti
    to_encode["type"] = "refresh"

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, PRIVATE_KEY, algorithm=ALGORITHM)
    return encoded_jwt, jti, expire

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def decode_refresh_token(token: str):
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
