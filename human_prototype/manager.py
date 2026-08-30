from dataclasses import dataclass, field
from typing import Optional
import uuid
from worker import Worker
from task import Task
from runner import Runner
from runnerPool import RunnerPool
import asyncio
import time
@dataclass()
class Manager:
    list_workers: list[Worker] = field(default_factory=list)
    program_dict: dict[str, Task] = field(default_factory=dict)
    background_tasks: set[asyncio.Task[bool]] = field(default_factory=set)
    runner_pool: RunnerPool = None

    def create_task(self, subprogram_name: str):
        #put subprogram_name in task
        return None
    
    def kill_program(self, program_name: str):
        for worker in self.list_workers:
            if worker.get_program_name() == program_name:
                worker.reset()
        self.program_dict.pop(program_name, None)
    
    def get_program_status(self, program_name: str) -> str:
        return self.program_dict[program_name].recursive_status()
    
    def add_workers(self, n: int = 4):
        for _ in range(n):
            self.list_workers.append(Worker(worker_id=str(uuid.uuid4()),runner_pool=self.runner_pool))
    
    def run_program(self, program_name: str):
        for worker in self.list_workers:
            if worker.get_program_name() == program_name:
                background_task = asyncio.create_task(worker.run())
                self.background_tasks.add(background_task)
                background_task.add_done_callback(self.background_tasks.discard)

  
                
               
    def partition_program(self, program_name: str):
        root_task = self.demo_partition()
        self.program_dict[program_name] = root_task
        tasks = [root_task, *root_task.list_subtasks]
        available_workers = self.get_available_workers()
        if len(available_workers) < len(tasks):
            raise RuntimeError(
                f"program {program_name!r} needs {len(tasks)} available workers"
            )
        for worker, task in zip(available_workers, tasks):
            worker.assign(program_name, task)

    def demo_partition(self) -> Task:
        task_2 = Task(assigned_program_name="demo/program1_t2.py", input_stream="7")
        task_3 = Task(assigned_program_name="demo/program1_t3.py", input_stream="7")
        return Task(
            assigned_program_name="demo/program1_t1.py",
            input_stream="7",
            orchestration_program_name="demo/program1_t1_or.py",
            list_subtasks=[task_2, task_3],
        )

    @classmethod
    async def demo(cls) -> "Manager":
        manager = cls()
        runnerPool = RunnerPool()
        manager.runner_pool = runnerPool
        stale_monitor = asyncio.create_task(runnerPool.poll_stales(interval=0.1))
        print("[INFO] Initializing environment")
        manager.add_workers(4)
        manager.runner_pool.add_runner(6)
        manager.partition_program("demo/program1")
        print("[INFO] Running program1")
        manager.run_program("demo/program1")
        for i in range(200):
            if i==25:
                print("Intentional runner[3] unhealthy...")
                runnerPool.list_runners[3].healthy = False
            print(manager.get_program_status("demo/program1"))
            await asyncio.sleep(0.1)
        stale_monitor.cancel()
        return manager

   

    def get_available_workers(self) -> Optional[list[Worker]]:
        available_workers: list[Worker] = []
        for worker in self.list_workers:
            if worker.is_available():
                available_workers.append(worker)
        return available_workers
    
    
    
    def get_worker_by_id(self, worker_id: str)->Worker:
        for worker in self.list_workers:
            if worker.worker_id==worker_id:
                return worker

if __name__=="__main__":
    manager = asyncio.run(Manager.demo())
    
    


    

    
