# NOTE TO READERS: Please go here for a white label, cleaner, more
# customizable, and better documented version of this code:
# https://github.com/vocodedev/vocode-python/tree/main/apps/telegram_bot

# SDxAI Hackathon 2023
# Team: Lumate
# Developer: Armando Santana
# Description: Life Coach voice chat prototype


import io
import os
import re
import pickle
import logging
import inspect
import base64
from replit import db
from typing import Tuple, Union, Optional, Dict, Type
from collections import defaultdict
from pydub import AudioSegment
from telegram import Update
from telegram.ext import (
  ApplicationBuilder,
  ContextTypes,
  CommandHandler,
  MessageHandler,
  filters,
)
from telegram.constants import ChatAction
from vocode.turn_based.transcriber import WhisperTranscriber, BaseTranscriber
from chat_gpt_agent import ChatGPTAgent
from coqui_synthesizer import CoquiSynthesizer
from vocode.turn_based.synthesizer import (
  #CoquiSynthesizer,
  StreamElementsSynthesizer,
  ElevenLabsSynthesizer,
  PlayHtSynthesizer,
  AzureSynthesizer,
  CoquiTTSSynthesizer,
  RimeSynthesizer,
  BaseSynthesizer,
)

logging.basicConfig(
  format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
  level=logging.INFO)

# Synthesizer class must be one of the following (do not change without adding support):
supported_classes = [
  CoquiSynthesizer,
  StreamElementsSynthesizer,
  ElevenLabsSynthesizer,
  PlayHtSynthesizer,
  AzureSynthesizer,
  CoquiTTSSynthesizer,
  RimeSynthesizer,
]
""" CONFIGURATION START """
# Required environment variables containing API key: OPENAI_API_KEY, TELEGRAM_BOT_KEY, and your Vocode synthesizers classes corresponding API key variable

# Your chosen synthesizer provider's corresponding Vocode turn_based class instance
SYNTH = CoquiSynthesizer(use_xtts=True)

# Array of tuples (synthesizer's voice id, nickname, description if text to voice)
DEFAULT_VOICES = [("9d455f7c-d4b3-485a-80bd-fa3efa21ec2a", "Scott",
                   ", Business Coach"),
                  ("070d6e3b-a45d-46de-9f21-b3e6889b5be9", "Kelly",
                   ", Personal Life Coach"),
                  ("88e2fd83-17e6-44b4-b91e-0d05556285d6", "Julie",
                   ", Fitness Coach")]


# Instructions to the language model responsible for generating response transcript.
SYSTEM_PROMPT = "You are having a voice-to-voice conversation with a user. Always pretend you are a real human. Do not avoid or decline to answer questions because you are not human."
""" CONFIGURATION END """


# key=synth class, value=attribute that chooses the provider voice
voice_attr_of: Dict[Type[BaseSynthesizer], str] = {
  ElevenLabsSynthesizer: "voice_id",
  PlayHtSynthesizer: "voice",
  StreamElementsSynthesizer: "voice",
  AzureSynthesizer: "voice_name",
  CoquiSynthesizer: "voice_id",
  CoquiTTSSynthesizer: "speaker",
  RimeSynthesizer: "speaker",
}
assert set(voice_attr_of.keys()) == set(
  supported_classes), "supported_classes must match the keys of voice_attr_of!"

assert (type(SYNTH) in voice_attr_of.keys()
        ), "Synthesizer class must be one of the supported ones!"
# Check voice_attr_of is correct by asserting all classes have their corresponding value as a parameter in the init function
for key, value in voice_attr_of.items():
  assert value in inspect.signature(key.__init__).parameters

# --------
# # Special defaultdict that supports replitdb
# class defaultdict:
#   def __init__(self, db) -> None:
#     # Initialize an empty dictionary to store user data
#     self.db = db
#
#   def __getitem__(self, chat_id) -> Dict:
#    # Return the user data for a given user id, or create a new one if not found
#     chat_id = str(chat_id)
#     default = {
#       "voices": DEFAULT_VOICES,
#       "current_voice": DEFAULT_VOICES[0],
#       "current_conversation": None,
#     }
#     if chat_id not in self.db.keys():
#       self.db[chat_id] = default
#       return default
#     return self.db[chat_id]
#
#   def __setitem__(self, chat_id, user_data) -> None:
#     # Set the user data for a given user id
#     self.db[chat_id] = user_data


class VocodeBotResponder:

  def __init__(
    self,
    transcriber: BaseTranscriber,
    system_prompt: str,
    synthesizer: BaseSynthesizer,
    db=None,
  ) -> None:
    self.transcriber = transcriber
    self.system_prompt = system_prompt
    self.synthesizer = synthesizer
    self.db = defaultdict(
      lambda: {
        "voices": DEFAULT_VOICES,
        "current_voice": DEFAULT_VOICES[0],
        "current_conversation": None,
      })

  def get_agent(self, chat_id: int) -> ChatGPTAgent:
    # Get current voice name and description from DB
    _, voice_name, voice_description = self.db[chat_id].get(
      "current_voice", (None, None, None))

    # Augment prompt based on available info
    prompt = self.system_prompt
    if voice_description != None or voice_name != None:
      prompt += "Pretend to be {0}. You are a trusted coach that must alway keep responses positive and offer good advice. Ask and help answer tough questions about the problem or challenge shared. Make sure your tone of voice remains optimistic and always provides some advice to get started.".format(
        voice_name + voice_description)

    # Load saved conversation if it exists
    convo_string = self.db[chat_id]["current_conversation"]
    agent = ChatGPTAgent(
      initial_message=
      "What challenges are you having? I'm here to help."
      .format(voice_name + voice_description),
      system_prompt=prompt,
      max_tokens=500,
      memory=pickle.loads(base64.b64decode(convo_string))
      if convo_string else None,
    )

    return agent

  def get_initial_greeting(self, chat_id: int) -> str:
    # Get current voice name and description from DB
    _, voice_name, voice_description = self.db[chat_id].get(
      "current_voice", (None, None, None))
    return "What challenges are you facing today?  I'm here to help.".format(
      voice_name + voice_description)

  async def fix_synthesize(self, message: str, chat_id: int):
    # Set current synthesizer voice from db
    voice_id, _, voice_description = self.db[chat_id].get(
      "current_voice", (None, None, None))
    # If we have a Coqui voice prompt, use that. Otherwise, set ID as synthesizer expects.
    if voice_id is not None:
      setattr(self.synthesizer, voice_attr_of[type(self.synthesizer)],
              voice_id)
    elif voice_description is not None and type(
        self.synthesizer) == CoquiSynthesizer:
      self.synthesizer.voice_prompt = voice_description
    # to fix pronunciation
    return await self.synthesizer.async_synthesize(message)

  async def send_voice_message(self, chat_id: int, message: str,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
    # Synthesize response
    synth_response = await self.fix_synthesize(message, chat_id)
    out_voice = io.BytesIO()
    synth_response.export(out_f=out_voice, format="ogg",
                          codec="libopus")  # type: ignore
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=agent_response)
    await context.bot.send_voice(chat_id=str(chat_id), voice=out_voice)

  # input can be audio segment or text
  async def get_response(
      self, chat_id: int,
      input: Union[str, AudioSegment]) -> Tuple[str, AudioSegment]:
    # If input is audio, transcribe it
    if isinstance(input, AudioSegment):
      input = self.transcriber.transcribe(input)

    # Get agent response
    agent = self.get_agent(chat_id)
    agent_response = agent.respond(input)

    # Synthesize response
    synth_response = await self.fix_synthesize(agent_response, chat_id)

    dump = pickle.dumps(agent.memory)
    encoded = base64.b64encode(dump).decode('ascii')
    # Save conversation to DB
    self.db[chat_id]["current_conversation"] = encoded

    return agent_response, synth_response

  async def handle_telegram_start(self, update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat, "Chat must be defined!"
    chat_id = update.effective_chat.id
    # Create user entry in DB
    self.db[chat_id] = {
      "voices": DEFAULT_VOICES,
      "current_voice": DEFAULT_VOICES[0],
      "current_conversation": None,
    }
    start_text = """
Welcome to Lumate, your digital life coach. How can I help you today?
"""
    await context.bot.send_message(chat_id=chat_id, text=start_text)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                       action=ChatAction.TYPING)
    greeting = self.get_initial_greeting(chat_id)
    await self.send_voice_message(chat_id, greeting, context)

  async def handle_telegram_message(
      self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("TEST")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                       action=ChatAction.TYPING)
    assert update.effective_chat, "Chat must be defined!"
    chat_id = update.effective_chat.id
    # Accept text or voice messages
    if update.message.voice:
      user_telegram_voice = await context.bot.get_file(
        update.message.voice.file_id)
      bytes = await user_telegram_voice.download_as_bytearray()
      # convert audio bytes to numpy array
      input = AudioSegment.from_file(
        io.BytesIO(bytes),
        format="ogg",
        codec="libopus"  # type: ignore
      )
    elif update.message and update.message.text:
      input = update.message.text
    else:
      # No audio or text, complain to user.
      await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="""
Sorry, I only respond to commands, voice, or text messages. Use /help for more information.""",
      )
      return

    # Get audio response from LLM/synth and reply
    agent_response, synth_response = await self.get_response(
      int(chat_id), input)
    out_voice = io.BytesIO()
    synth_response.export(out_f=out_voice, format="ogg",
                          codec="libopus")  # type: ignore
    #await context.bot.send_message(chat_id=update.effective_chat.id,
    #                               text=agent_response)
    await context.bot.send_voice(chat_id=str(chat_id), voice=out_voice)

  async def handle_telegram_select_voice(
      self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat, "Chat must be defined!"
    chat_id = str(update.effective_chat.id)
    if not (context.args):
      await context.bot.send_message(
        chat_id=chat_id,
        text="You must include a voice id. Use /list to list available voices",
      )
      return
    new_voice_id = context.args[0]

    user_voices = self.db[chat_id]["voices"]
    if len(user_voices) <= int(new_voice_id):
      await context.bot.send_message(
        chat_id=chat_id,
        text=
        "Sorry, I do not recognize that voice. Use /list to list available voices.",
      )
      return
    else:
      self.db[chat_id]["current_voice"] = DEFAULT_VOICES[int(new_voice_id)]
      #self.db[chat_id]["current_voice"] = user_voices[int(new_voice_id)]

      #self.db[chat_id].current_voice = user_voices[int(new_voice_id)]

      # Reset conversation
      self.db[chat_id]["current_conversation"] = None
      await context.bot.send_message(chat_id=chat_id,
                                     text="Voice changed successfully!")
      await context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                         action=ChatAction.TYPING)
      # get greeting and send audio of it using those two functions
      greeting = self.get_initial_greeting(chat_id)
      await self.send_voice_message(chat_id, greeting, context)

  # async def handle_telegram_create_voice(
  #     self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  #   assert update.effective_chat, "Chat must be defined!"
  #   chat_id = update.effective_chat.id
  #   if type(self.synthesizer) is not CoquiSynthesizer:
  #     await context.bot.send_message(
  #       chat_id=chat_id,
  #       text="Sorry, voice creation is only supported for Coqui TTS.",
  #     )
  #     return
  #   if not (context.args):
  #     await context.bot.send_message(
  #       chat_id=chat_id,
  #       text="You must include a voice description.",
  #     )
  #     return

  #   voice_description = " ".join(context.args)

  #   # Coqui voices are created at synthesis-time, so don't have an ID nor name.
  #   new_voice = (None, None, voice_description)
  #   self.db[chat_id]["voices"].append(new_voice)
  #   self.db[chat_id]["current_voice"] = new_voice
  #   # Reset conversation
  #   self.db[chat_id]["current_conversation"] = None

  #   await context.bot.send_message(chat_id=chat_id,
  #                                  text="Voice changed successfully!")

  async def handle_telegram_list_voices(
      self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat, "Chat must be defined!"
    chat_id = update.effective_chat.id
    user_voices = self.db[chat_id]["voices"]  # array (id, name, description)
    # Make string table of id, name, description
    voices = "\n".join([
      f"{id}: {name if name else ''}{f' - {description}' if description else ''}"
      for id, (internal_id, name, description) in enumerate(user_voices)
    ])
    await context.bot.send_message(chat_id=chat_id,
                                   text=f"Available voices:\n{voices}")

  async def handle_telegram_who(self, update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat, "Chat must be defined!"
    chat_id = update.effective_chat.id
    _, name, description = self.db[chat_id].get("current_voice",
                                                (None, None, None))
    
    current = name if name else description
    await context.bot.send_message(
      chat_id=chat_id,
      text=f"I am currently '{current}'.",
    )


  
  async def handle_telegram_help(self, update: Update,
                                 context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = """
I'm a voice chatbot, here to talk with you! Here's what you can do:

- Send me a voice message and I'll respond with a voice message.
- Use /list to see a list of available voices.
- Use /voice <voice_id> to change the voice I use to respond and reset the conversation.
- Use /who to see what voice I currently am.
- Use /help to see this help message again.
"""
    assert update.effective_chat, "Chat must be defined!"
    # if type(self.synthesizer) is CoquiSynthesizer:
    #   help_text += "\n- Use /create <voice_description> to create a new Coqui TTS voice from a text prompt and switch to it."
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=help_text)

  async def handle_telegram_unknown_cmd(
      self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat, "Chat must be defined!"
    await context.bot.send_message(
      chat_id=update.effective_chat.id,
      text="""
Sorry, I didn\'t understand that command. Use /help to see available commands""",
    )


if __name__ == "__main__":
  transcriber = WhisperTranscriber()
  print("the db", db)
  voco = VocodeBotResponder(transcriber, SYSTEM_PROMPT, SYNTH, db)
  application = ApplicationBuilder().token(
    os.environ["TELEGRAM_BOT_KEY"]).build()
  application.add_handler(CommandHandler("start", voco.handle_telegram_start))
  application.add_handler(
    MessageHandler(~filters.COMMAND, voco.handle_telegram_message))
  # application.add_handler(
  #   CommandHandler("create", voco.handle_telegram_create_voice))
  application.add_handler(
    CommandHandler("voice", voco.handle_telegram_select_voice))
  application.add_handler(
    CommandHandler("list", voco.handle_telegram_list_voices))
  application.add_handler(CommandHandler("who", voco.handle_telegram_who))
  application.add_handler(CommandHandler("help", voco.handle_telegram_help))
  application.add_handler(
    MessageHandler(filters.COMMAND, voco.handle_telegram_unknown_cmd))
  application.run_polling()
