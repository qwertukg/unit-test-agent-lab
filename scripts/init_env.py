"""Create local service secrets. Never prints secrets; preserves existing values."""
import secrets
from pathlib import Path

from dotenv import dotenv_values


def main():
    path = Path(".env")
    values = dict(dotenv_values(".env.example"))
    if path.exists():
        values.update(dotenv_values(path))
    for key in ("POSTGRES_PASSWORD", "GITLAB_ROOT_PASSWORD", "API_TOKEN", "WEBHOOK_SECRET", "MODEL_API_KEY", "GATEWAY_KEY"):
        if not values.get(key):
            values[key] = ("sk-" if key == "MODEL_API_KEY" else "") + secrets.token_hex(24)
    values["DATABASE_URL"] = "postgresql://lab:" + values["POSTGRES_PASSWORD"] + "@localhost:55432/lab"
    # Escape single quotes for python-dotenv; keys are not printed.
    path.write_text("\n".join(k + "='" + (v or "").replace("\\", "\\\\").replace("'", "\\'") + "'" for k, v in values.items()) + "\n", encoding="utf-8")
    print("Local .env prepared. Set CLOUD_MODEL, CLOUD_API_KEY, CLOUD_BASE_URL and TOKEN_PRICE_UPPER_USD locally.")


if __name__ == "__main__":
    main()
