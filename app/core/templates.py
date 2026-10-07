"""
Templates Engine & Compatibility Adapter for SmartBot.

Provides backwards and forwards compatibility between Starlette/FastAPI versions:
- Legacy signature: TemplateResponse(name, context, ...)
- Modern signature: TemplateResponse(request, name, context, ...)
                    or TemplateResponse(request=request, name=name, context=context, ...)

This completely eliminates "TypeError: unhashable type: 'dict'" across all Python
and Starlette versions (including Python 3.11/3.12/3.13 on Render, Heroku, Docker, and local).
"""
import inspect
from typing import Any, Mapping, Optional
import starlette.templating
from starlette.requests import Request
from starlette.responses import Response

_orig_template_response = starlette.templating.Jinja2Templates.TemplateResponse


def _safe_template_response(self, *args, **kwargs) -> Response:
    """
    Intelligent adapter for Jinja2Templates.TemplateResponse.
    Accepts any combination of arguments and correctly maps them to the underlying
    Starlette implementation.
    """
    # Case 1: First positional argument is a string (legacy calling pattern: name, context)
    if args and isinstance(args[0], str):
        name = args[0]
        context = args[1] if len(args) > 1 else kwargs.pop("context", {})
        rest_args = args[2:]

        request = kwargs.pop("request", None)
        if request is None and isinstance(context, dict):
            request = context.get("request")

        if not isinstance(context, dict):
            context = {}
        if request is not None and "request" not in context:
            context["request"] = request

        # Try modern Starlette: (request, name, context, ...)
        if request is not None:
            try:
                return _orig_template_response(self, request, name, context, *rest_args, **kwargs)
            except (TypeError, AttributeError):
                pass

            # Try keyword arguments: (request=request, name=name, context=context, ...)
            try:
                return _orig_template_response(self, request=request, name=name, context=context, *rest_args, **kwargs)
            except TypeError:
                pass

        # Fallback to legacy Starlette signature: (name, context, ...)
        return _orig_template_response(self, name, context, *rest_args, **kwargs)

    # Case 2: First positional argument is Request (modern positional: request, name, context)
    if args and isinstance(args[0], Request):
        request = args[0]
        name = args[1] if len(args) > 1 else kwargs.pop("name", None)
        context = args[2] if len(args) > 2 else kwargs.pop("context", {})
        rest_args = args[3:]

        if isinstance(context, dict) and "request" not in context:
            context["request"] = request

        try:
            return _orig_template_response(self, request, name, context, *rest_args, **kwargs)
        except TypeError:
            # Running on older Starlette expecting (name, context)
            return _orig_template_response(self, name, context, *rest_args, **kwargs)

    # Case 3: Keyword arguments: TemplateResponse(request=request, name="...", context={...})
    if "name" in kwargs:
        request = kwargs.get("request")
        context = kwargs.get("context", {})
        if isinstance(context, dict) and request and "request" not in context:
            context["request"] = request

        try:
            return _orig_template_response(self, *args, **kwargs)
        except TypeError:
            # Legacy Starlette doesn't accept 'request' as kwarg
            kw = dict(kwargs)
            req = kw.pop("request", None)
            name_val = kw.pop("name")
            return _orig_template_response(self, name_val, context, *args, **kw)

    return _orig_template_response(self, *args, **kwargs)


def patch_jinja2_templates():
    """Apply monkey-patch globally to Starlette's Jinja2Templates."""
    if starlette.templating.Jinja2Templates.TemplateResponse is not _safe_template_response:
        starlette.templating.Jinja2Templates.TemplateResponse = _safe_template_response


# Apply automatically on import
patch_jinja2_templates()


class Jinja2Templates(starlette.templating.Jinja2Templates):
    """Subclass with explicit safe TemplateResponse support."""
    TemplateResponse = _safe_template_response


# Shared singleton instance
templates = Jinja2Templates(directory="app/templates")
