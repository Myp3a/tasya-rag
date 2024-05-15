import aiohttp
import json
import os
from aiohttp import web

from langchain.globals import set_debug

from history import get_history, MessagesWrapper
from llm import llm
import agents
import config
from ya_integration import is_whisper, get_whisper_audio
from translator import translate

if config.DEBUG:
    set_debug(True)

routes = web.RouteTableDef()

used_agents_list: list[agents.Agent] = [
    agents.WeatherAgent(llm),
    agents.SearchAgent(llm),
    agents.ChatterAgent(llm),
]

@routes.post("/generate")
async def generate_text(req):
    data = await req.post()
    if not (history := data.get("history")):
        return web.json_response({"error": "history is required"}, status=400)
    history = MessagesWrapper(history.split("\n"))
    supervisor = agents.SupervisorAgent(llm, used_agents_list)
    chosen_worker = supervisor.ask(history)
    match chosen_worker:
        case "meteorologist":
            resp = agents.WeatherAgent(llm).ask(history)
        case "researcher":
            resp = agents.SearchAgent(llm).ask(history)
        case "chatter":
            resp = agents.ChatterAgent(llm).ask(history)
        case _:
            resp = "I couldn't understand you. Please, try again."
    return web.json_response({"text": resp})

async def generate_text_with_history(query, session_id):
    if (to := config.TRANSLATE):
        query = await translate(query, to, "en")
    history = get_history(session_id)
    history.add_user_message(query)
    supervisor = agents.SupervisorAgent(llm, used_agents_list)
    chosen_worker = supervisor.ask(history)
    match chosen_worker:
        case "meteorologist":
            resp = agents.WeatherAgent(llm).ask(history)
        case "researcher":
            resp = agents.SearchAgent(llm).ask(history)
        case "chatter":
            resp = agents.ChatterAgent(llm).ask(history)
        case _:
            resp = "I couldn't understand you. Please, try again."
    history.add_ai_message(resp)
    if (to := config.TRANSLATE):
        resp = await translate(resp, "en", to)
    return resp

@routes.post("/text_input")
async def text_input(req: web.Request):
    data = await req.post()
    if not (session_id := data.get("session_id")):
        return web.json_response({"error": "session_id is required"}, status=400)
    if not (query := data.get("query")):
        return web.json_response({"error": "query is required"}, status=400)
    resp = await generate_text_with_history(query, session_id)
    return web.json_response({"text": resp})

@routes.post("/voice_input")
async def voice_input(req):
    async with aiohttp.ClientSession() as sess:
        data = await req.post()
        found_text = json.loads(data["text"])["text"]
        found_text = found_text.split("Тася")[-1]
        if found_text.startswith(", "):
            found_text = found_text.replace(", ", "", 1)
        audio_data = data["file"]
        if not os.path.isdir("tmp"):
            os.mkdir("tmp")
        with open("tmp/input.wav", "wb") as outf:
            outf.write(audio_data)
        generated_text = await generate_text_with_history(found_text, 0)
        whisper = await is_whisper("tmp/input.wav")
        if whisper:
            await get_whisper_audio(generated_text)
        else:
            async with sess.post(f"http://{config.XTTS_API_SERVER_HOST}:{config.XTTS_API_SERVER_PORT}/tts_to_audio/",
                                 json={"text": generated_text, "speaker_wav": "kelex", "language": "ru"}
            ) as resp:
                with open('tmp/output.wav', "wb") as outf:
                    outf.write(await resp.read())
        async with sess.post(f"http://{config.VOICE_PLAYER_HOST}:{config.VOICE_PLAYER_PORT}/voice_play",
                             data={"file": open('tmp/output.wav', 'rb')}
        ) as req:
            pass

app = web.Application()
app.add_routes(routes)
web.run_app(app, port=8085)