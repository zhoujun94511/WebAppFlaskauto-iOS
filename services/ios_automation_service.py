"""UI automation: locate elements (accessibility id / predicate / xpath / …)
and act on them (tap / type / pinch), plus foreground-app info and page source.

This is the 'programmable' layer on top of coordinate control — borrowed in
spirit from facebook-wda's selector API, implemented against our WDAController.
"""

from __future__ import annotations

from services import get_adapter
from services.request_validators import require
from utils.app_errors import AppError, ErrorCode


class IOSAutomationService:
    @staticmethod
    def _ctrl(udid: str):
        return get_adapter().controller(udid)

    @classmethod
    def _locate(cls, udid: str, body: dict) -> tuple:
        require(body, "using", "value")
        controller = cls._ctrl(udid)
        uuid = controller.find_element(str(body["using"]), str(body["value"]))
        if not uuid:
            raise AppError(ErrorCode.BAD_REQUEST,
                           f"element not found: {body['using']}={body['value']}")
        return controller, uuid

    @classmethod
    def find(cls, udid: str, body: dict) -> dict:
        require(body, "using", "value")
        controller = cls._ctrl(udid)
        uuid = controller.find_element(str(body["using"]), str(body["value"]))
        if not uuid:
            return {"found": False}
        return {"found": True, **controller.element_info(uuid)}

    @classmethod
    def tap(cls, udid: str, body: dict) -> dict:
        controller, uuid = cls._locate(udid, body)
        controller.click_element(uuid)
        return {"tapped": True, "uuid": uuid}

    @classmethod
    def type_text(cls, udid: str, body: dict) -> dict:
        require(body, "text")
        controller, uuid = cls._locate(udid, body)
        if body.get("clear"):
            controller.clear_element(uuid)
        controller.set_element_value(uuid, str(body["text"]))
        return {"typed": len(str(body["text"])), "uuid": uuid}

    @classmethod
    def pinch(cls, udid: str, body: dict) -> dict:
        require(body, "scale")
        controller, uuid = cls._locate(udid, body)
        controller.pinch_element(uuid, float(body["scale"]), float(body.get("velocity", 1.0)))
        return {"pinched": True, "scale": float(body["scale"])}

    @classmethod
    def foreground(cls, udid: str) -> dict:
        return cls._ctrl(udid).active_app_info()

    @classmethod
    def source(cls, udid: str) -> dict:
        return {"source": cls._ctrl(udid).source()}
