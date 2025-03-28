import importlib
import json
import os
import re
import dotenv
import copy
from abc import ABC, abstractmethod
from typing import Any, Dict, List
from datetime import datetime
from services.call_context import CallContext
from services.event_emitter import EventEmitter
from logger_config import get_logger
import anthropic
from openai import AsyncOpenAI

dotenv.load_dotenv(override=True)

logger = get_logger("LLMService")

from pydantic import BaseModel

class StructuredOutput(BaseModel):
    first_name: str
    last_name: str
    company_name: str
    email: str
    current_phone_working: bool
    alternative_phone_number: str
    date_time_in_UK: str

class AbstractLLMService(EventEmitter, ABC):
    def __init__(self, context: CallContext):
        super().__init__()
        self.system_prompt = context.system_prompt
        self.initial_message = context.initial_message
        self.context = context
        self.tools = context.functions
        self.user_context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": self.initial_message}
        ]
        self.partial_response_index = 0
        self.available_functions = {}
        self.sentence_buffer = ""
        context.user_context = self.user_context

    def set_call_context(self, context: CallContext):
        self.context = context
        self.system_prompt = context.system_prompt
        self.initial_message = context.initial_message
        self.tools = context.functions
        self.user_context = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": self.initial_message}
        ]
        context.user_context = self.user_context
        for tool in self.tools:
            function_name = tool["function"]["name"]
            tool_name = "webhook" if function_name != "transfer_call" and function_name != "end_call" else function_name
            module = importlib.import_module(f"functions.{tool_name}")
            self.available_functions[function_name] = getattr(module, tool_name)

    @abstractmethod
    async def summarize_context(self):
        pass

    async def completion(self, text: str, interaction_count: int, role: str = 'user', name: str = 'user'):
        pass

    def reset(self):
        self.partial_response_index=0

    def validate_function_args(self, args):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            logger.info('Warning: Invalid function arguments returned by LLM:', args)
            return {}

 
    def split_into_sentences(self, text):
        # Split the text into sentences, keeping the separators
        sentences = re.split(r'([.!?])', text)
        # Pair the sentences with their separators
        sentences = [''.join(sentences[i:i+2]) for i in range(0, len(sentences), 2)]
        return sentences

    async def emit_complete_sentences(self, text, interaction_count):
        self.sentence_buffer += text
        sentences = self.split_into_sentences(self.sentence_buffer)
        
        # Emit all complete sentences
        for sentence in sentences[:-1]:
            await self.emit('llmreply', {
                "partialResponseIndex": self.partial_response_index,
                "partialResponse": sentence.strip()
            }, interaction_count)
            self.partial_response_index += 1
        
        # Keep the last (potentially incomplete) sentence in the buffer
        self.sentence_buffer = sentences[-1] if sentences else ""

class OpenAIService(AbstractLLMService):
    def __init__(self, context: CallContext):
        super().__init__(context)
        self.openai = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    async def summarize_context(self):
        try:
            messages = [{"role": "system", "content": "Here is the call history between AI and customer. You have to summarize it shortly within 4 sentences. Cearly and shortly. User is the customer and Assistant is the AI agent."}]
            messages.append({"role": "user", "content": f"Here is the history: {json.dumps(self.user_context)}"})
            context_summary = await self.openai.chat.completions.create(
                model = "gpt-4o-mini",
                messages = messages,
                max_tokens = 1500,
                temperature = 0.7
            )
            self.context.summary = context_summary.choices[0].message.content
            return context_summary.choices[0].message.content
        except Exception as e:
            print(e)
            return ""

    async def get_structured_output(self):
        try:
            messages = [{"role": "system", "content": "Here is the call history between AI and customer. You have to extract the structured output from the conversation. User is the customer and Assistant is the AI agent."}]
            messages.append({"role": "user", "content": f"Here is the history: {json.dumps(self.user_context)}"})
            response = await self.openai.beta.chat.completions.parse(
                model="gpt-4o-mini", 
                messages=messages, 
                response_format=StructuredOutput
            )
            return response.choices[0].message.parsed
        except Exception as e:
            print(e)
            return ""
        
    async def completion(self, text: str, interaction_count: int, role: str = 'user', name: str = 'user'):
        try:
            self.user_context.append({"role": role, "content": text, "name": name})
            messages = [{"role": "system", "content": self.system_prompt}] + self.user_context
            
            if role == "function":
                stream = await self.openai.chat.completions.create(
                    model = "gpt-4o-mini",
                    messages = messages,
                    stream = True
                )
            else:
                stream = await self.openai.chat.completions.create(
                    model = "gpt-4o-mini",
                    messages = messages,
                    tools = self.tools,
                    stream = True
                )

            complete_response = ""
            function_name = ""
            function_args = ""

            async for chunk in stream:
                delta = chunk.choices[0].delta
                content = delta.content or ""
                tool_calls = delta.tool_calls

                if tool_calls:
                    for tool_call in tool_calls:
                        if tool_call.function:
                            logger.info(f"Function call detected as: {tool_call.function.name}")
                            function_name = tool_call.function.name or function_name
                            function_args = tool_call.function.arguments or function_args
                else:
                    complete_response += content
                    await self.emit_complete_sentences(content, interaction_count)

                if chunk.choices[0].finish_reason == "tool_calls":
                    logger.info(f"Function call detected: {function_name}")
                    function_to_call = self.available_functions[function_name]
                    function_args = self.validate_function_args(function_args)
                    function_args["transfer_number"] = self.context.transfer_phone_number
                    tool_data = next((tool for tool in self.tools if tool['function']['name'] == function_name), None)
                    say = tool_data['function']['say']
                    await self.emit('llmreply', {
                        "partialResponseIndex": None,
                        "partialResponse": say
                    }, interaction_count)

                    self.user_context.append({"role": "assistant", "content": say})
                    context = copy.deepcopy(self.context)  
                    if function_name != "end_call" and function_name != "transfer_call":
                        context.function_metadata = context.function_metadata[function_name]
                    
                    function_response = await function_to_call(context, function_args)
                    print("function response: ", function_response)              
                    logger.info(f"Function {function_name} called with args: {function_args}")

                    if function_name != "end_call":
                        await self.completion(function_response, interaction_count, 'function', function_name)

            if self.sentence_buffer.strip():
                await self.emit('llmreply', {
                    "partialResponseIndex": self.partial_response_index,
                    "partialResponse": self.sentence_buffer.strip()
                }, interaction_count)
                self.sentence_buffer = ""
                
            self.user_context.append({"role": "assistant", "content": complete_response, "name": "assistant"})

        except Exception as e:
            print(e)
            return None
        