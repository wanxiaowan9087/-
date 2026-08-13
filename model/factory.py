from abc import ABC, abstractmethod
from typing import Optional

from langchain_community.embeddings import DashScopeEmbeddings

from utils.config_handler import rag_config
from langchain_community.chat_models import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self)->Optional[BaseChatModel|Embeddings]:
        pass

class ChatModelFactory(BaseModelFactory):
    def generator(self)->Optional[BaseChatModel|Embeddings]:
        return ChatTongyi(model=rag_config["chat_model_name"])

class EmbeddingsFactory(BaseModelFactory):
    def generator(self)->Optional[Embeddings|BaseChatModel]:
        return DashScopeEmbeddings(model=rag_config["embedding_model_name"])

chat_model_factory = ChatModelFactory()
embedding_model_factory = EmbeddingsFactory()

