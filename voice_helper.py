import asyncio

import speech_recognition as sr
import time
from speech_recognition import WaitTimeoutError

voices_for_sleep = [
    'пк в спящий режим',
    'комп в спящий режим',
    'компьютер в спящий режим',
    'в спящий режим',
    'спящий режим',
]
voices_for_off = [
    'выключи пк',
    'выключи комп',
    'выключи компьютер',
    'выруби пк',
    'выруби комп',
    'выруби компьютер',
]


class Diana:
    def __init__(self, server=None):
        self.server = server
        self.recognizer = sr.Recognizer()
        self.microphone = self.get_default_microphone()
        self.diane_names = ['диана', 'диан', 'дианочка', 'лисингтон', 'лиса', 'лисичка']

    def get_default_microphone(self):
        mic = sr.Microphone()
        print(f"\nИспользуется микрофон по умолчанию: {mic.device_index}")

        r = sr.Recognizer()
        with mic as source:
            print("🎚️ Калибрую микрофон...")
            r.adjust_for_ambient_noise(source, duration=1)
        print("✅ Микрофон готов!\n")

        return mic

    def listen(self):
        try:
            with self.microphone as source:
                print("🎤 СЛУШАЮ... (говорите сейчас)")
                audio = self.recognizer.listen(source, timeout=10)

            text = self.recognizer.recognize_google(audio, language='ru-RU').lower()
            return text

        except WaitTimeoutError:
            print("⏱ Вы слишком долго не говорили")
            return None
        except sr.UnknownValueError:
            return None
        except Exception as e:
            print(f"🔧 Ошибка: {e}")
            return None

    def process_lisington_style(self, text):
        if not any(word in text for word in self.diane_names):
            return

        if any(word in text for word in voices_for_sleep):
            if self.server:
                asyncio.run_coroutine_threadsafe(self.server.system_sleep(), self.server.loop)
                return

        elif any(word in text for word in voices_for_off):
            if self.server:
                self.server.system_shutdown()
                return

    def run(self):
        while True:
            time.sleep(0.5)
            text = self.listen()
            if text:
                self.process_lisington_style(text)
