from dataclasses import dataclass, field
from typing import Optional
import uuid
from runner import Runner
import asyncio
import time

@dataclass
class RunnerPool:
    list_runners: list[Runner] = field(default_factory=list)
    free_runners: asyncio.Queue[Runner] = field(
        default_factory=asyncio.Queue
    )
    stale_runners: asyncio.Queue[Runner] = field(
        default_factory=asyncio.Queue
    )
    
    
    async def poll_stales(self, interval: float = 1):
        while True:
            runner = await self.stale_runners.get()
            print(
                f"[INFO] runner pool: checking stale runner: {runner.id}"
            )
            if runner.get_health():
                self.free_runners.put_nowait(runner)
                print(
                    f"[INFO] runner pool: runner: {runner.id} is healthy "
                    "and available"
                )
            else: 
                self.stale_runners.put_nowait(runner)
                print(
                    f"[INFO] runner pool: runner: {runner.id} remains stale"
                )
            await asyncio.sleep(interval)



    def add_runner(self, n: int = 4):
        for _ in range(n):
            runner = Runner(id=uuid.uuid4())
            self.list_runners.append(runner)
            self.free_runners.put_nowait(runner)
            print(
                f"[INFO] runner pool: added runner: {runner.id} "
                "to the available pool"
            )
    
    

    async def get_runner(self, timeout: Optional[int] = None):
        print("[INFO] runner pool: waiting to hand off an available runner")
        runner = await self.free_runners.get() 
        print(f"[INFO] runner pool: handed off runner: {runner.id}")
        return runner

    async def put_runner(self, runner):
        if runner in self.list_runners:
            if runner.get_health():
                self.free_runners.put_nowait(runner)
                print(
                    f"[INFO] runner pool: runner: {runner.id} returned "
                    "to the available pool"
                )
            else: 
                self.stale_runners.put_nowait(runner)
                print(
                    f"[INFO] runner pool: runner: {runner.id} moved "
                    "to the stale pool"
                )
        else:
            print(
                f"[ERROR] runner pool: runner: {runner.id} does not "
                "belong to this pool"
            )
            raise AttributeError(f"runner {runner.id} not found in pool.")
