# pylint: disable=redefined-outer-name,no-member

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import alembic
import pytest
from alembic.autogenerate.compare import comparators
from parse import parse
from sqlalchemy import create_engine, text

from alembic_utils import TEST_VERSIONS_ROOT

PYTEST_DB = "postgresql://alem_user:password@localhost:5680/alem_db"


@pytest.fixture(scope="session")
def maybe_start_pg() -> None:
    """Creates a postgres 12 docker container that can be connected
    to using the PYTEST_DB connection string"""

    container_name = "alembic_utils_pg"
    image = "postgres:12"

    connection_template = "postgresql://{user}:{pw}@{host}:{port:d}/{db}"
    conn_args = parse(connection_template, PYTEST_DB)

    # Don't attempt to instantiate a container if
    # we're on CI
    if "GITHUB_SHA" in os.environ:
        yield
        return

    try:
        is_running = (
            subprocess.check_output(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_name]
            )
            .decode()
            .strip()
            == "true"
        )
    except subprocess.CalledProcessError:
        # Can't inspect container if it isn't running
        is_running = False

    if is_running:
        yield
        return

    subprocess.call(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "-p",
            f"{conn_args['port']}:5432",
            "-d",
            "-e",
            f"POSTGRES_DB={conn_args['db']}",
            "-e",
            f"POSTGRES_PASSWORD={conn_args['pw']}",
            "-e",
            f"POSTGRES_USER={conn_args['user']}",
            "--health-cmd",
            "pg_isready",
            "--health-interval",
            "3s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "15",
            image,
        ]
    )
    # Wait for postgres to become healthy
    for _ in range(10):
        out = subprocess.check_output(["docker", "inspect", container_name])
        inspect_info = json.loads(out)[0]
        health_status = inspect_info["State"]["Health"]["Status"]
        if health_status == "healthy":
            break
        else:
            time.sleep(1)
    else:
        raise Exception("Could not reach postgres comtainer. Check docker installation")
    yield
    # subprocess.call(["docker", "stop", container_name])
    return


@pytest.fixture(scope="session")
def engine(maybe_start_pg: None):
    """sqlalchemy engine fixture"""
    eng = create_engine(PYTEST_DB)
    yield eng
    eng.dispose()


@pytest.fixture(scope="function")
def reset(engine):
    """Fixture to reset between tests"""

    def run_cleaners():
        comparators._registry = {
            (target, qualifier): [func for func in funcs if "pg_function" not in func.__name__]
            for (target, qualifier), funcs in comparators._registry.items()
        }

        engine.execute("drop schema public cascade; create schema public;")

        # Remove any migrations that were left behind
        TEST_VERSIONS_ROOT.mkdir(exist_ok=True, parents=True)
        shutil.rmtree(TEST_VERSIONS_ROOT)
        TEST_VERSIONS_ROOT.mkdir(exist_ok=True, parents=True)
        # engine.execute(DROP_ALL_FUNCTIONS_SQL)

    run_cleaners()

    yield

    run_cleaners()
