"""Shared demo access; admitted visitors share one workspace, without account isolation."""
import hashlib
import hmac
import os

import streamlit as st
from dotenv import load_dotenv

from .config import ROOT


def require_demo_access():
    load_dotenv(ROOT / ".env")
    code = os.getenv("DEEPDIVE_DEMO_ACCESS_CODE", "")
    if not code and not os.getenv("RAILWAY_ENVIRONMENT_ID"):
        return False
    if len(code) < 24:
        st.error("Demo access is not configured. Contact the project owner.")
        st.stop()

    stamp = hashlib.sha256(code.encode()).hexdigest()

    def sign_in():
        supplied = st.session_state.pop("_demo_code_input", "")
        if hmac.compare_digest(supplied.encode(), code.encode()):
            st.session_state["_demo_access"] = stamp
            st.session_state.pop("_demo_login_error", None)
        else:
            st.session_state["_demo_login_error"] = True

    if st.session_state.get("_demo_access") != stamp:
        st.title("DeepDive")
        st.caption("Enter the demo code supplied with the project link.")
        with st.form("demo_login", clear_on_submit=True):
            st.text_input("Demo access code", type="password", key="_demo_code_input")
            st.form_submit_button("Open workspace", on_click=sign_in)
        if st.session_state.get("_demo_login_error"):
            st.error("The demo code is incorrect.")
        st.stop()

    st.sidebar.button("Sign out", on_click=st.session_state.clear)
    st.info("Shared hackathon demo: everyone with the code can view and manage this workspace. "
            "Use public or sample documents.")
    return True
