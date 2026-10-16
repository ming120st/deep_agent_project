"""Intranet post pipeline (content -> safe HTML).

    from intranet_post import PostDocument, render_post
    rendered = render_post(document_dict_or_model)
    rendered.body_html   # goes into the device job as parameters.body_html
    rendered.images      # keys/filenames the uploader must provide
"""
from .models import SCHEMA_VERSION, PostDocument
from .renderer import ALLOWED_ATTRS, ALLOWED_TAGS, ImageRef, RenderedPost, RenderError, render_post
from .safety import is_safe_html, safe_url
from .themes import THEMES

__all__ = [
    "ALLOWED_ATTRS",
    "ALLOWED_TAGS",
    "SCHEMA_VERSION",
    "THEMES",
    "ImageRef",
    "PostDocument",
    "RenderError",
    "RenderedPost",
    "is_safe_html",
    "render_post",
    "safe_url",
]
