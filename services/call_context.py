from typing import List, Optional, Dict, Any

class CallContext:
    """
    A class that represents a call context.
    """
    def __init__(self):
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.call_ended: bool = False
        self.user_context: List = []
        self.system_prompt: str = ""
        self.initial_message: str = ""
        self.start_time: Optional[str] = None
        self.end_time: Optional[str] = None
        self.final_status: Optional[str] = None
        self.file_names: Optional[List[str]] = None
        self.calendar_link: Optional[str] = None
        self.transfer_phone_number: Optional[str] = None
        self.client_number: str = ""
        self.functions: List = None
        self.function_metadata: Optional[Dict] = None
