import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Вставь сюда токен от @BotFather
TOKEN = "8878586697:AAGcrlCXBMqSvnSLMQlBZM7asiqC2MPcmss"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 Привет! Я бот проекта «Битва богов» для Standoff 2!\n\n"
        "Скоро здесь будет доступен функционал анализа матчей, подсчета рейтинга и проведения турниров."
    )

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
