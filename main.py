import eel
import sqlite3
import os
import requests  # Для запросов к GitHub API
import zipfile  # Для распаковки обновления
import shutil  # Для замены старых файлов
from aiogram import Bot, Dispatcher
from aiogram.types import InputFile
import base64
from io import BytesIO

DB_PATH = 'config.db'
FILE_STORAGE = 'uploaded_files'  # Папка для хранения файлов
UPDATE_URL = "https://api.github.com/repos/<user>/<repo>/releases/latest"

if not os.path.exists(FILE_STORAGE):
    os.makedirs(FILE_STORAGE)

# Инициализация Eel
eel.init('web')

# Функция для создания базы данных при первом запуске
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Таблица для конфигурации
    cursor.execute('''CREATE TABLE IF NOT EXISTS config
                      (id INTEGER PRIMARY KEY, bot_token TEXT, user_id TEXT)''')

    # Таблица для хранения файлов и message_id
    cursor.execute('''CREATE TABLE IF NOT EXISTS files
                      (file_name TEXT, message_id INTEGER)''')

    conn.commit()
    conn.close()

# Функция для сохранения конфигурации в базу данных
def save_config(bot_token, user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (id, bot_token, user_id) VALUES (1, ?, ?)", (bot_token, user_id))
    conn.commit()
    conn.close()

# Функция для получения конфигурации из базы данных
def get_config():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT bot_token, user_id FROM config WHERE id = 1")
    config = cursor.fetchone()
    conn.close()
    return config

# Функция для проверки, настроена ли конфигурация
@eel.expose
def is_configured():
    config = get_config()
    return config is not None

# Первичная настройка
@eel.expose
def setup(bot_token, user_id):
    save_config(bot_token, user_id)
    return "Конфигурация выполнена успешна!"

# Функция для загрузки файла в Telegram чат пользователя
@eel.expose
def upload_file(file_name, file_data):
    config = get_config()
    if not config:
        return "Конфигурация не обнаружена. Пожалуйста выполните настройку повторно."
    
    bot_token, user_id = config
    bot = Bot(token=bot_token)
    dp = Dispatcher(bot)
    
    try:
        # Декодируем base64 данные файла
        file_content = base64.b64decode(file_data.split(',')[1])  # file_data - это data URL, нужно отделить метаданные
        file_path = os.path.join(FILE_STORAGE, file_name)

        with open(file_path, 'wb') as f:
            f.write(file_content)

        # Отправка файла в Telegram
        with open(file_path, 'rb') as file:
            input_file = InputFile(file)
            message = dp.loop.run_until_complete(bot.send_document(chat_id=user_id, document=input_file))

        # Сохраняем имя файла и message_id в базу данных
        save_file_to_db(file_name, message.message_id)

        return f"Файл {file_name} выгружен в облако успешно!"
    except Exception as e:
        return f"Ошибка выгрузки файла: {str(e)}"

# Функция для сохранения файла и message_id в базу данных
def save_file_to_db(file_name, message_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO files (file_name, message_id) VALUES (?, ?)", (file_name, message_id))
    conn.commit()
    conn.close()

# Получить список сохраненных файлов
@eel.expose
def get_saved_files():
    return os.listdir(FILE_STORAGE)

# Удалить файл
@eel.expose
def delete_file(file_name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Получаем message_id по имени файла
    cursor.execute("SELECT message_id FROM files WHERE file_name = ?", (file_name,))
    result = cursor.fetchone()

    if result:
        message_id = result[0]

        # Удаляем сообщение в Telegram
        config = get_config()
        if not config:
            return "Конфигурация не обнаружена."

        bot_token, user_id = config
        bot = Bot(token=bot_token)

        try:
            bot.delete_message(chat_id=user_id, message_id=message_id)
        except Exception as e:
            return f"Ошибка удаления файла с чата: {str(e)}"

        # Удаляем запись из базы данных и файл с локального диска
        cursor.execute("DELETE FROM files WHERE file_name = ?", (file_name,))
        conn.commit()

        file_path = os.path.join(FILE_STORAGE, file_name)
        if os.path.exists(file_path):
            os.remove(file_path)

        return f"Файл {file_name} удалён успешно!"
    else:
        return "Файл не найден."

    conn.close()

@eel.expose
def check_for_update():
    try:
        response = requests.get(UPDATE_URL)
        data = response.json()
        latest_version = data["tag_name"]  # Получаем последнюю версию

        # Проверяем текущую версию программы (пусть она будет в файле version.txt)
        with open("version.txt", "r") as version_file:
            current_version = version_file.read().strip()

        if current_version < latest_version:
            return f"Доступно обновление до версии {latest_version}. Вы хотите обновить?"
        else:
            return "У вас уже установлена последняя версия."
    except Exception as e:
        return f"Ошибка при проверке обновлений: {str(e)}"

@eel.expose
def get_current_version():
    try:
        with open("version.txt", "r") as version_file:
            current_version = version_file.read().strip()
        return current_version
    except Exception as e:
        return f"Ошибка получения версии: {str(e)}"

# Скачивание и установка обновлений
@eel.expose
def download_update():
    try:
        response = requests.get(UPDATE_URL)
        data = response.json()
        download_url = data["assets"][0]["browser_download_url"]  # Получаем ссылку на скачивание

        # Скачиваем zip-файл обновления
        update_response = requests.get(download_url, stream=True)
        update_path = "update.zip"
        with open(update_path, "wb") as update_file:
            update_file.write(update_response.content)

        # Распаковываем обновление
        with zipfile.ZipFile(update_path, 'r') as zip_ref:
            zip_ref.extractall("update_temp")

        # Заменяем старые файлы новыми
        for root, dirs, files in os.walk("update_temp"):
            for file in files:
                shutil.move(os.path.join(root, file), os.path.join(os.getcwd(), file))

        # Удаляем временные файлы обновления
        shutil.rmtree("update_temp")
        os.remove(update_path)

        return "Программа успешно обновлена!"
    except Exception as e:
        return f"Ошибка при обновлении программы: {str(e)}"

# Основной код для запуска Eel
def main():
    init_db()
    eel.start('index.html', size=(700, 600))

if __name__ == "__main__":
    main()
