"""Read deployment settings from environment variables or Streamlit secrets."""
import os


def get_setting(name, default=""):
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        import streamlit as st
        return str(st.secrets.get(name, default))
    except Exception:
        return default
