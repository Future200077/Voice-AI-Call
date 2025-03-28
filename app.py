import asyncio
import base64
from datetime import datetime
import dotenv
import json
import os
from collections import deque
from typing import Dict, List, Optional
from services.email_service import send_email_smtp
import pytz

import chatbot_config
from services.event_emitter import EventEmitter
from services.stream_service import StreamService
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse
from logger_config import get_logger
from services.llm_service import OpenAIService
from services.call_context import CallContext
from services.tts_service import ElevenLabsTTS
from services.transcription_service import TranscriptionService

dotenv.load_dotenv(override=True)
app = FastAPI()
logger = get_logger("App")

global call_contexts
call_contexts = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/incoming")
async def incoming_call(request: Request) -> HTMLResponse:
    try:
        start_time = datetime.now(pytz.timezone('Europe/London'))
        hour = start_time.hour
        server = os.environ.get("SERVER")
        form_data = await request.form()
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        direction = form_data.get("Direction")
        twilio_number = direction == "inbound" and to_number or from_number
        client_number = direction == "inbound" and from_number or to_number

        print("twilio_number: %s, client_number: %s", twilio_number, client_number)

        if hour >= 5 and hour < 12:
            initial_message = direction == "inbound" and chatbot_config.inbound_initial_message_1 or chatbot_config.outbound_initial_message
        elif hour >= 12 and hour < 17:
            initial_message = direction == "inbound" and chatbot_config.inbound_initial_message_2 or chatbot_config.outbound_initial_message
        elif hour >= 17 and hour < 22:
            initial_message = direction == "inbound" and chatbot_config.inbound_initial_message_3 or chatbot_config.outbound_initial_message
        else:
            initial_message = direction == "inbound" and chatbot_config.inbound_initial_message_1 or chatbot_config.outbound_initial_message
        
        twilio_client = Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))
        response = VoiceResponse()
        connect = Connect()
        stream = connect.stream(url=f"wss://{server}/connection")
        stream.parameter(name="system_prompt", value=chatbot_config.system_prompt)
        stream.parameter(name="initial_message", value=initial_message)
        stream.parameter(name="file_names", value=chatbot_config.file_names)
        stream.parameter(name="functions", value=json.dumps(chatbot_config.functions))
        stream.parameter(name="direction", value=direction)
        stream.parameter(name="start_time", value=start_time)
        stream.parameter(name="client_number", value=client_number)
        stream.parameter(name="transfer_phone_number", value=chatbot_config.transfer_phone_number)
        stream.parameter(name="calendar_link", value=chatbot_config.calendar_link)
        response.append(connect)
        return HTMLResponse(content=str(response), status_code=200)
        
    except Exception as e:
        print(e)

        return HTMLResponse(content="")

@app.websocket("/connection")
async def connection(websocket: WebSocket):
    await websocket.accept()

    stream_service = StreamService(websocket)
    transcription_service = TranscriptionService()
    llm_service = OpenAIService(CallContext())
    tts_service = ElevenLabsTTS()
    
    marks = deque()
    interaction_count = 0
    
    await transcription_service.connect()

    async def process_media(msg):
        await transcription_service.send(base64.b64decode(msg["media"]["payload"]))
    
    async def handle_transcription(text):
        nonlocal interaction_count
        if not text:
            return
        # if the text exists, send it to the LLM
        await llm_service.completion(text, interaction_count)
        interaction_count += 1

    async def handle_llm_reply(llm_reply, interaction_count):
        await tts_service.generate(llm_reply, interaction_count)

    async def handle_speech(response_index, audio, label, interaction_count):
        await stream_service.buffer(response_index, audio)

    async def handle_audio_sent(mark_label):
        marks.append(mark_label)

    async def handle_utterance(text, stream_sid):
        try:
            if len(marks) > 0 and text.strip():
                logger.info("Intruption detected from the user, clearing system.")
                await websocket.send_json({
                    "streamSid": stream_sid,
                    "event": "clear"
                })
                #reset states
                stream_service.reset()
                llm_service.reset()

        except Exception as e:
            logger.error(f"Error while handling utterance: {e}")
            e.print_stack()

    transcription_service.on("transcription", handle_transcription)
    transcription_service.on("utterance", handle_utterance)
    llm_service.on("llmreply", handle_llm_reply)
    tts_service.on("speech", handle_speech)
    stream_service.on("audiosent", handle_audio_sent)

    message_queue = asyncio.Queue()

    async def websocket_listener():
        try:
            while True:
                data = await websocket.receive_text()
                await message_queue.put(json.loads(data))
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")

    async def process_messages():
        while True:
            msg = await message_queue.get()
            if msg["event"] == "start": 
                stream_sid = msg["start"]["streamSid"]
                call_sid = msg["start"]["callSid"]
                system_prompt = msg["start"]["customParameters"]["system_prompt"]
                initial_message = msg["start"]["customParameters"]["initial_message"]
                file_names = msg["start"]["customParameters"]["file_names"]
                functions = json.loads(msg["start"]["customParameters"]["functions"])
                start_time = msg["start"]["customParameters"]["start_time"]
                client_number = msg["start"]["customParameters"]["client_number"]
                direction = msg["start"]["customParameters"]["direction"]
                calendar_link = msg["start"]["customParameters"]["calendar_link"]
                transfer_phone_number = msg["start"]["customParameters"]["transfer_phone_number"]
                system_prompt = "The date and time at the moment is: " + datetime.now(pytz.timezone('Europe/London')).strftime("%d/%m/%Y %H:%M:%S") + ". " + system_prompt
                print("system_prompt: ", system_prompt)
                call_context = CallContext()

                if os.getenv("RECORD_CALLS") == "true":
                    get_twilio_client().calls(call_sid).recordings.create({"recordingChannels": "dual"})

                # Decide if the call the call was initiated from the UI or is an inbound
                if call_sid not in call_contexts:
                    # Inbound call
                    call_context.stream_sid = stream_sid
                    call_context.call_sid = call_sid
                    call_context.system_prompt = system_prompt
                    call_context.initial_message = initial_message
                    call_context.file_names = file_names
                    call_context.functions = functions
                    call_context.start_time = start_time
                    call_context.end_time = start_time
                    call_context.client_number = client_number
                    call_context.direction = direction
                    call_context.calendar_link = calendar_link
                    call_context.transfer_phone_number = transfer_phone_number
                    call_contexts[call_sid] = call_context
                else:
                    # Outbound call
                    pass
                llm_service.set_call_context(call_context)
                print("llm_service: ", llm_service.context.initial_message)
                stream_service.set_stream_sid(stream_sid)
                transcription_service.set_stream_sid(stream_sid)

                logger.info(f"Twilio -> Starting Media Stream for {stream_sid}")
                await tts_service.generate({
                    "partialResponseIndex": None,
                    "partialResponse": call_context.initial_message
                }, 1)

            elif msg["event"] == "media":
                asyncio.create_task(process_media(msg))

            elif msg["event"] == "mark":
                label = msg["mark"]["name"]
                if label in marks:
                    marks.remove(label)   

            elif msg['event'] == 'stop':
                call_context.end_time = llm_service.context.end_time
                call_context.summary = await llm_service.summarize_context()
                structured_output = await llm_service.get_structured_output()
                if structured_output.current_phone_working:
                    available_phone_number = llm_service.context.client_number
                else:
                    available_phone_number = structured_output.alternative_phone_number
                email_text = "first_name: " + structured_output.first_name + "\n" + "last_name: " + structured_output.last_name + "\n" + "company_name: " + structured_output.company_name + "\n" + "email: " + structured_output.email + "\n" + "phone: " + available_phone_number + "\n" + "date_time_in_UK: " + llm_service.context.start_time + "\n" + "summary: " + call_context.summary
                send_email_smtp("aiexpert0208@gmail.com", "Call Summary", email_text)
                # if call_context.calendar_link is not None and call_context.calendar_link != "":
                #     sms_message = await send_calendar_message(call_context.client_number, json.dumps(call_context.user_context), call_context.calendar_link)
                #     call_context.user_context.append(sms_message)
                # await update_chat_history(call_context)
                del call_contexts[call_context.call_sid]
                logger.info(f"Twilio -> Media stream {stream_sid} ended.")
                break
            message_queue.task_done()

    try:
        listen_task = asyncio.create_task(websocket_listener())
        process_task = asyncio.create_task(process_messages())
        await asyncio.gather(listen_task, process_task)
    except Exception as e:
        print(e)
    finally:
        await transcription_service.disconnect()

def get_twilio_client():
    return Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))

