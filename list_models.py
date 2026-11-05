"""
List the Gemini models your API key can use for text generation.

    python list_models.py

Copy one name into GEMINI_MODEL_NAME in .env. Free-tier daily limits differ per
model, and "flash-lite" models usually allow far more free requests than the
full "flash" models.
"""

import google.generativeai as genai

from app import config


def main() -> None:
    if not config.GEMINI_API_KEY:
        print("GEMINI_API_KEY is empty. Add it to .env first.")
        return
    genai.configure(api_key=config.GEMINI_API_KEY)
    print("Models that support generateContent:\n")
    for model in genai.list_models():
        if "generateContent" in getattr(model, "supported_generation_methods", []):
            print(model.name.replace("models/", ""))


if __name__ == "__main__":
    main()
