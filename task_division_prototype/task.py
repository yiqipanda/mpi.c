from dataclasses import dataclass, field
from typing import Final

import asyncio


TIME_INTERVAL = 0.1
@dataclass
class Task:
    main_output: str = ""
    input_stream: str = ""
    intermediate_stream: list[str] = field(default_factory=list)
    output_stream: str = ""
    assigned_program_name: Final[str] = ""
    orchestration_program_name: str = ""
    list_subtasks: Final[list[Task]] = field(default_factory=list)
    completed: bool = False
    

    def recursive_status(self)->str:
        children_status = ""
        for task in self.list_subtasks:
            children_status += task.recursive_status()
        return self.assigned_program_name + " status = " + str(self.completed) + " " + children_status

    def has_completed(self):
        return self.completed

    def get_input_stream(self):
        return self.input_stream #copy in future
    
    def set_input_stream(self, stream: str):
        self.input_stream = stream 

    def set_output_stream(self, stream: str):
        self.output_stream = stream

    def get_output_stream(self):
        return self.output_stream #copy in future
    
    def set_main_output(self, output: str):
        self.main_output = output


    def get_intermediate_stream(self) -> list[str]:
        return list(self.intermediate_stream)

    async def update_intermediate_stream(self): #assume its called after task assigned program is done
        if self.get_intermediate_stream():
            return
        not_done = True
        while not_done:
            print(f"[INFO] waiting for assigned program: {self.assigned_program_name} children to finish")
            not_done = False
            for subtask in self.list_subtasks:
                if not subtask.completed:
                    not_done = True

            await asyncio.sleep(TIME_INTERVAL)
        self.intermediate_stream.append(self.input_stream.strip())
        self.intermediate_stream.append(self.main_output.strip())
        for subtask in self.list_subtasks:
            self.intermediate_stream.append(subtask.output_stream.strip())
        
        
