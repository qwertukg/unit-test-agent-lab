from langgraph.checkpoint.postgres import PostgresSaver

from agent_lab.config import Settings
from agent_lab.store import Store


def main():
    store = Store(Settings().database_url.get_secret_value())
    with store.lock(0, 17012027) as lock:
        if lock is None:
            raise RuntimeError("another migrator is active")
        store.setup()
        with PostgresSaver.from_conn_string(store.dsn) as saver:
            saver.setup()
    print("Application and LangGraph migrations complete")


if __name__ == "__main__":
    main()
