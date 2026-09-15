# ruff: noqa: I001


#        DEVELOPER TEXT
# Chaos testing is when a particular module suddenly distrupted basically
# Current tests concern Runner health, sudden program kill and runner pool race 
# Sep 14th, all tests passed elligible to use

import asyncio
import random
import sys
from pathlib import Path

import pytest


HUMAN_PROTOTYPE = Path(__file__).resolve().parents[2] / "human_prototype"
sys.path.insert(0, str(HUMAN_PROTOTYPE))

from manager import Manager
from worker import MAX_RETRIES


@pytest.mark.asyncio
async def test_runner_random_unhealthy_1():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        workers = manager.list_workers[:3]
        for _ in range(100):
            if all(
                worker.current_runner is not None
                and worker.current_runner.process is not None
                for worker in workers
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("demo workers did not start")

        await asyncio.sleep(random.Random(101).uniform(0.1, 0.4))
        target = random.Random(102).choice(workers)
        failed_runner = target.current_runner
        assert failed_runner is not None
        failed_runner.healthy = False

        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=15)
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"
        assert failed_runner.healthy is False
        assert manager.runner_pool.stale_runners.qsize() == 1
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_runner_random_unhealthy_2():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        workers = manager.list_workers[:3]
        for _ in range(100):
            if all(
                worker.current_runner is not None
                and worker.current_runner.process is not None
                for worker in workers
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("demo workers did not start")

        await asyncio.sleep(random.Random(201).uniform(0.1, 0.4))
        targets = random.Random(202).sample(workers, 2)
        failed_runners = [worker.current_runner for worker in targets]
        assert all(runner is not None for runner in failed_runners)
        for runner in failed_runners:
            runner.healthy = False

        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=15)
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"
        assert len({runner.id for runner in failed_runners}) == 2
        assert manager.runner_pool.stale_runners.qsize() == 2
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_runner_random_unhealthy_3():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        workers = manager.list_workers[:3]
        for _ in range(100):
            if all(
                worker.current_runner is not None
                and worker.current_runner.process is not None
                for worker in workers
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("demo workers did not start")

        await asyncio.sleep(random.Random(301).uniform(0.1, 0.4))
        failed_runners = [worker.current_runner for worker in workers]
        assert all(runner is not None for runner in failed_runners)
        for runner in failed_runners:
            runner.healthy = False

        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=15)
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"
        assert len({runner.id for runner in failed_runners}) == 3
        assert manager.runner_pool.stale_runners.qsize() == 3
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_runner_random_unhealthy_4():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        target = random.Random(4).choice(manager.list_workers[:3])
        failed_runner_ids = []
        for _ in range(2):
            for _ in range(200):
                runner = target.current_runner
                if (
                    runner is not None
                    and runner.process is not None
                    and runner.process.returncode is None
                    and runner.id not in failed_runner_ids
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("worker did not receive a replacement runner")
            failed_runner_ids.append(runner.id)
            runner.healthy = False

        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=15)
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"
        assert len(set(failed_runner_ids)) == 2
        assert manager.runner_pool.stale_runners.qsize() == 2
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_runner_random_unhealthy_5():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        target = random.Random(5).choice(manager.list_workers[:3])
        failed_runner_ids = []
        for _ in range(MAX_RETRIES):
            for _ in range(200):
                runner = target.current_runner
                if (
                    runner is not None
                    and runner.process is not None
                    and runner.process.returncode is None
                    and runner.id not in failed_runner_ids
                ):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("worker did not receive the expected runner")
            failed_runner_ids.append(runner.id)
            runner.healthy = False

        target_index = manager.list_workers.index(target)
        target_result = await asyncio.wait_for(
            asyncio.shield(jobs[target_index]),
            timeout=5,
        )
        assert target_result is False
        assert root_task.completed is False
        assert len(set(failed_runner_ids)) == MAX_RETRIES
        assert manager.runner_pool.stale_runners.qsize() == MAX_RETRIES
        manager.kill_program("demo/program1")
        await asyncio.wait_for(
            asyncio.gather(*jobs, return_exceptions=True),
            timeout=3,
        )
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_program_kill_1():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        for _ in range(400):
            if root_task.main_output and not root_task.intermediate_stream:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("root task did not reach the child-wait phase")

        manager.kill_program("demo/program1")
        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=3)
        assert "demo/program1" not in manager.program_dict
        assert root_task.completed is False
        assert False in results
        assert all(worker.assigned_task is None for worker in manager.list_workers)
        assert all(worker.current_runner is None for worker in manager.list_workers)
        assert all(worker.available for worker in manager.list_workers)
        assert manager.runner_pool.free_runners.qsize() == 6
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_program_kill_2():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    root_worker = manager.list_workers[0]
    jobs = manager.run_program("demo/program1")
    try:
        for _ in range(600):
            runner = root_worker.current_runner
            if (
                runner is not None
                and runner.program_name == "demo/program1_t1_or.py"
                and runner.process is not None
                and runner.process.returncode is None
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("root task did not reach orchestration")

        manager.kill_program("demo/program1")
        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=3)
        assert "demo/program1" not in manager.program_dict
        assert root_task.completed is False
        assert results[0] is False
        assert all(worker.assigned_task is None for worker in manager.list_workers)
        assert all(worker.current_runner is None for worker in manager.list_workers)
        assert all(worker.available for worker in manager.list_workers)
        assert manager.runner_pool.free_runners.qsize() == 6
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_program_kill_3():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        await asyncio.sleep(0.005)
        manager.kill_program("demo/program1")
        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=3)
        assert results == [False, False, False]
        assert "demo/program1" not in manager.program_dict
        assert root_task.completed is False
        assert all(worker.assigned_task is None for worker in manager.list_workers)
        assert all(worker.current_runner is None for worker in manager.list_workers)
        assert all(worker.available for worker in manager.list_workers)
        assert manager.runner_pool.free_runners.qsize() == 6
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_program_kill_4():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    jobs = manager.run_program("demo/program1")
    try:
        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=12)
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"

        manager.kill_program("demo/program1")
        assert "demo/program1" not in manager.program_dict
        assert root_task.completed is True
        assert all(worker.assigned_task is None for worker in manager.list_workers)
        assert all(worker.current_runner is None for worker in manager.list_workers)
        assert all(worker.available for worker in manager.list_workers)
        assert manager.runner_pool.free_runners.qsize() == 6
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_delay", range(1, 11))
async def test_runner_absence_1(recovery_delay):
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    for runner in manager.runner_pool.list_runners:
        runner.healthy = False
    jobs = manager.run_program("demo/program1")
    try:
        await asyncio.sleep(recovery_delay)
        recovery_runner = random.Random(600 + recovery_delay).choice(
            manager.runner_pool.list_runners
        )
        recovery_runner.healthy = True

        results = await asyncio.wait_for(
            asyncio.gather(*jobs),
            timeout=recovery_delay + 20,
        )
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"
        assert recovery_runner.healthy is True
        assert sum(runner.healthy for runner in manager.runner_pool.list_runners) == 1
        assert (
            manager.runner_pool.free_runners.qsize()
            + manager.runner_pool.stale_runners.qsize()
            == 6
        )
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_runner_absence_2():
    manager = await Manager.demo(setup_only=True)
    root_task = manager.program_dict["demo/program1"]
    root_worker = manager.list_workers[0]
    jobs = manager.run_program("demo/program1")
    try:
        for _ in range(600):
            active_runner = root_worker.current_runner
            if (
                active_runner is not None
                and active_runner.program_name == "demo/program1_t1_or.py"
                and active_runner.process is not None
                and active_runner.process.returncode is None
            ):
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("root task did not reach orchestration")

        while not manager.runner_pool.free_runners.empty():
            runner = manager.runner_pool.free_runners.get_nowait()
            runner.healthy = False
            manager.runner_pool.stale_runners.put_nowait(runner)
        active_runner.healthy = False

        await asyncio.sleep(1)
        recovery_runner = random.Random(702).choice(
            manager.runner_pool.list_runners
        )
        recovery_runner.healthy = True

        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=12)
        assert results == [True, True, True]
        assert root_task.completed is True
        assert root_task.output_stream.strip() == "24"
        assert recovery_runner.healthy is True
        assert sum(runner.healthy for runner in manager.runner_pool.list_runners) == 1
        assert (
            manager.runner_pool.free_runners.qsize()
            + manager.runner_pool.stale_runners.qsize()
            == 6
        )
    finally:
        await manager.shutdown()
