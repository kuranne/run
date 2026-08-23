from pathlib import Path
from typing import List, Optional
from util.config import Config
from .handler_interface import LanguageHandler
from .custom_language_handler import CustomLanguageHandler

class HandlerRegistry:
    """
    Registry for managing and selecting language handlers.
    Custom configurations from Run.toml take highest priority.
    """

    def __init__(self, custom_handler: Optional[CustomLanguageHandler] = None):
        self.custom_handler = custom_handler or CustomLanguageHandler()
        self.handlers: List[LanguageHandler] = [self.custom_handler]

    def register(self, handler: LanguageHandler):
        """
        Register a new language handler.

        Args:
            handler (LanguageHandler): Handler instance to register.
        """
        self.handlers.append(handler)

    def get_handler(self, path: Path, config: Config) -> Optional[LanguageHandler]:
        """
        Find handler for a single file.

        Args:
            path (Path): Path to source file.
            config (Config): Configuration object.

        Returns:
            Optional[LanguageHandler]: Matching handler if found.
        """
        for handler in self.handlers:
            if handler.can_handle(path, config):
                return handler
        return None
