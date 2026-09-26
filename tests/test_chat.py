import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.api.v1.chat import get_chat_service
from app.api.v1.models import get_model_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.indices import IndexCreate
from app.schemas.models import ModelWrite
from app.schemas.projects import ProjectCreate
from app.services.chat import ChatService
from app.services.documents import DocumentError, DocumentService
from app.services.generation import GenerationService
from app.services.indices import IndexService
from app.services.models import ModelService
from app.services.projects import ProjectService


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / 'test.db')
        self.chat = ChatService(self.settings)
        self.models = ModelService(self.settings)
        self.project = ProjectService(self.settings).create_project(ProjectCreate(name='Test')).id
        app = create_app()
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        app.dependency_overrides[get_model_service] = lambda: self.models
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        settings_patch = patch('app.api.v1.services.get_settings', return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.actor = {'X-User-Email': 'admin@example.com'}
        self.embed = self.models.save(self.project, ModelWrite(name='Embed', provider='ollama', model='embeddinggemma'))
        self.generate = self.models.save(self.project, ModelWrite(name='Answer', provider='ollama', model='test-chat', purpose='generation'))
        self.service_id = str(uuid4())
        self.base = f'/api/v1/projects/{self.project}/services/{self.service_id}'
        self.service_payload = dict(name='Service', members=[{'email': 'admin@example.com', 'role': 'admin'}, {'email': 'member@example.com', 'role': 'member'}],
                                    embedding_model_id=self.embed.id, generation_model_id=self.generate.id)
        saved = self.client.put(self.base, json=self.service_payload, headers=self.actor)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.index = IndexService(self.settings).create(self.project, IndexCreate(name='Index', service_id=self.service_id, service_name='Service'))
        self.docs = DocumentService(self.settings)
        with patch.object(DocumentService, 'embed', return_value=[[1.0, 0.0]]):
            self.doc = self.docs.ingest(self.project, 'policy.txt', b'Vacation is 20 days.', self.index.id)
        self.embed_patch = patch.object(DocumentService, 'embed', return_value=[[1.0, 0.0]])
        self.embed_mock = self.embed_patch.start()
        self.addCleanup(self.embed_patch.stop)

    def conversation(self):
        response = self.client.post(self.base + '/conversations', json={'index_ids': [self.index.id]}, headers=self.actor)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def ask(self, conversation, request_id=None, question='How much vacation?'):
        return self.client.post(f'/api/v1/conversations/{conversation}/messages', headers=self.actor,
                                json={'question': question, 'client_request_id': request_id or str(uuid4())})

    def search(self, **overrides):
        return self.client.post(self.base + '/search', headers=self.actor,
                                json=dict(question='Vacation?', index_ids=[self.index.id]) | overrides)

    def test_search_ranks_and_scopes_documents(self):
        with patch.object(DocumentService, 'embed', return_value=[[0.0, 1.0]]):
            irrelevant = self.docs.ingest(self.project, 'other.txt', b'Another topic.', self.index.id)
        outside = IndexService(self.settings).create(self.project, IndexCreate(name='Private', service_id=str(uuid4()), service_name='Private'))
        self.docs.ingest(self.project, 'secret.txt', b'Private document.', outside.id)
        response = self.search(top_k=2)
        self.assertEqual(response.status_code, 200, response.text)
        sources = response.json()['sources']
        self.assertEqual([item['document_id'] for item in sources], [self.doc.id, irrelevant.id])
        self.assertEqual(sources[0]['content'], 'Vacation is 20 days.')
        self.assertEqual(self.search(index_ids=[outside.id]).status_code, 422)
        self.assertEqual(self.search(index_ids=[]).status_code, 422)
        self.assertEqual(self.search(top_k=21).status_code, 422)
        self.assertEqual(self.client.post(self.base + '/search', headers={'X-User-Email': 'outside@example.com'}, json={'question': 'x', 'index_ids': [self.index.id]}).status_code, 403)

    def test_mixed_models_and_dimensions_rejected_before_embedding(self):
        with closing(self.docs.connect()) as connection, connection:
            connection.execute('UPDATE documents SET embedding_model = ? WHERE id = ?', ('other-model', self.doc.id))
        self.assertEqual(self.search().status_code, 409)
        self.embed_mock.assert_not_called()
        with closing(self.docs.connect()) as connection, connection:
            connection.execute('UPDATE documents SET embedding_model = ?, embedding_dimensions = 3 WHERE id = ?', ('embeddinggemma', self.doc.id))
        self.assertEqual(self.search().status_code, 409)

    def test_mixed_index_spaces_rejected(self):
        second = self.docs.ingest(self.project, 'different.txt', b'Test', self.index.id)
        with closing(self.docs.connect()) as connection, connection:
            connection.execute('UPDATE documents SET embedding_provider = ? WHERE id = ?', ('openai', second.id))
        self.embed_mock.reset_mock()
        self.assertEqual(self.search().status_code, 409)
        self.embed_mock.assert_not_called()

    def test_invalid_persisted_vectors_return_actionable_error(self):
        with closing(self.docs.connect()) as connection, connection:
            connection.execute("UPDATE chunks SET embedding = '[0, 0]' WHERE document_id = ?", (self.doc.id,))
        self.assertEqual(self.search().status_code, 409)

    def test_answer_persistence_citations_and_idempotency(self):
        conversation = self.conversation()
        request_id = str(uuid4())
        with patch.object(GenerationService, 'generate', return_value='20일입니다. [1]') as generator:
            answer = self.ask(conversation, request_id)
            self.assertEqual(answer.status_code, 200, answer.text)
            self.assertEqual(answer.json()['status'], 'completed')
            self.assertEqual(answer.json()['citations'][0]['document_id'], self.doc.id)
            self.assertEqual(self.ask(conversation, request_id).json(), answer.json())
            self.assertEqual(generator.call_count, 1)
            self.assertEqual(self.ask(conversation, request_id, 'Different').status_code, 409)
        messages = self.client.get(f'/api/v1/conversations/{conversation}/messages', headers=self.actor).json()
        self.assertEqual(messages, [answer.json()])
        with closing(self.chat.connect()) as connection:
            stored = connection.execute('SELECT citations FROM chat_messages').fetchone()[0]
        self.assertNotIn('Vacation', stored)
        listed = self.client.get(self.base + '/conversations', headers=self.actor).json()
        self.assertEqual(listed[0]['title'], 'How much vacation?')

    def test_followup_rewrites_search_question(self):
        conversation = self.conversation()
        with patch.object(GenerationService, 'generate', return_value='20일입니다. [1]'):
            self.assertEqual(self.ask(conversation).status_code, 200)
        with patch.object(GenerationService, 'generate', side_effect=['휴가 일수는?', '20일입니다. [1]']) as generator:
            self.assertEqual(self.ask(conversation, question='How many again?').status_code, 200)
            self.assertEqual(generator.call_count, 2)
            self.assertIn('previous_questions', generator.call_args_list[0].args[3])
            self.embed_mock.assert_called_with(['휴가 일수는?'])

    def test_invalid_citation_fails_and_same_request_can_retry(self):
        conversation = self.conversation()
        request_id = str(uuid4())
        with patch.object(GenerationService, 'generate', return_value='Answer [99]'):
            self.assertEqual(self.ask(conversation, request_id).status_code, 503)
        self.assertEqual(self.client.get(f'/api/v1/conversations/{conversation}/messages', headers=self.actor).json(), [])
        with patch.object(GenerationService, 'generate', return_value='Answer [1]'):
            self.assertEqual(self.ask(conversation, request_id).status_code, 200)

    def test_no_documents_and_insufficient_evidence(self):
        conversation = self.conversation()
        with patch.object(GenerationService, 'generate', return_value='INSUFFICIENT_EVIDENCE'):
            answer = self.ask(conversation)
            self.assertEqual(answer.json()['status'], 'insufficient_evidence')
            self.assertEqual(answer.json()['citations'], [])
        self.docs.delete_document(self.project, self.doc.id)
        # A new conversation avoids a follow-up rewriting call.
        with patch.object(GenerationService, 'generate') as generator:
            answer = self.ask(self.conversation())
            self.assertEqual(answer.json()['status'], 'no_sources')
            generator.assert_not_called()

    def test_owner_scope_revocation_and_deleted_source(self):
        conversation = self.conversation()
        with patch.object(GenerationService, 'generate', return_value='Answer [1]'):
            self.assertEqual(self.ask(conversation).status_code, 200)
        url = f'/api/v1/conversations/{conversation}/messages'
        self.assertEqual(self.client.get(url, headers={'X-User-Email': 'member@example.com'}).status_code, 404)
        self.docs.delete_document(self.project, self.doc.id)
        message = self.client.get(url, headers=self.actor).json()[0]
        self.assertEqual(message['status'], 'unavailable')
        self.assertFalse(message['citations'][0]['available'])
        self.assertNotIn('Vacation', message['citations'][0]['content'])
        with closing(self.chat.connect()) as connection, connection:
            connection.execute('UPDATE service_settings SET members = ? WHERE id = ?', ('[]', self.service_id))
        self.assertEqual(self.client.get(url, headers=self.actor).status_code, 403)

    def test_delete_conversation_cascades_messages(self):
        conversation = self.conversation()
        with patch.object(GenerationService, 'generate', return_value='Answer [1]'):
            self.assertEqual(self.ask(conversation).status_code, 200)
        self.assertEqual(self.client.delete(f'/api/v1/conversations/{conversation}', headers=self.actor).status_code, 204)
        with closing(self.chat.connect()) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM chat_messages').fetchone()[0], 0)

    def test_service_model_purpose_and_references(self):
        response = self.client.put(self.base, headers=self.actor, json=self.service_payload | {'generation_model_id': self.embed.id})
        self.assertEqual(response.status_code, 422)
        url = f'/api/v1/projects/{self.project}/models/{self.generate.id}'
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.assertEqual(self.client.put(url, json={'name': 'Changed', 'provider': 'ollama', 'model': 'different', 'purpose': 'generation'}).status_code, 409)
        with self.assertRaises(DocumentError):
            self.models.embedding_settings(self.project, self.generate.id)

    def test_model_connection_test_embedding_and_generation(self):
        base = f'/api/v1/projects/{self.project}/models'
        response = self.client.post(f'{base}/{self.embed.id}/test')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('2차원', response.json()['detail'])
        with patch.object(GenerationService, 'generate', return_value='OK'):
            self.assertEqual(self.client.post(f'{base}/{self.generate.id}/test').status_code, 200)

    def test_openai_response_adapter_and_sanitized_error(self):
        model = self.models.save(self.project, ModelWrite(name='Remote', provider='openai', purpose='generation', model='account-model', api_key='private-key'))
        with patch('app.services.generation.httpx.Client') as provider:
            post = provider.return_value.__enter__.return_value.post
            post.return_value.json.return_value = {'status': 'completed', 'output': [{'type': 'reasoning'}, {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Answer'}]}]}
            self.assertEqual(GenerationService(self.settings).generate(self.project, model.id, 'Rules', 'Question'), 'Answer')
            self.assertEqual(post.call_args.args[0], 'https://api.openai.com/v1/responses')
            self.assertFalse(post.call_args.kwargs['json']['store'])
            response = httpx.Response(401, request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
            post.return_value.raise_for_status.side_effect = httpx.HTTPStatusError('private-key', request=response.request, response=response)
            with self.assertRaises(DocumentError) as raised:
                GenerationService(self.settings).generate(self.project, model.id, 'Rules', 'Question')
            self.assertNotIn('private-key', raised.exception.detail)

    def test_ollama_adapter(self):
        with patch('app.services.generation.httpx.Client') as provider:
            post = provider.return_value.__enter__.return_value.post
            post.return_value.json.return_value = {'done': True, 'message': {'content': 'Answer'}}
            self.assertEqual(GenerationService(self.settings).generate(self.project, self.generate.id, 'Rules', 'Question'), 'Answer')
            self.assertFalse(post.call_args.kwargs['json']['stream'])
            self.assertTrue(post.call_args.args[0].endswith('/api/chat'))

    def test_duplicate_inflight_request_is_blocked(self):
        conversation = self.conversation()
        request_id = str(uuid4())
        def respond(*args):
            self.assertEqual(self.ask(conversation, request_id).status_code, 409)
            return 'Answer [1]'
        with patch.object(GenerationService, 'generate', side_effect=respond) as generator:
            self.assertEqual(self.ask(conversation, request_id).status_code, 200)
            self.assertEqual(generator.call_count, 1)

    def test_revocation_during_generation_prevents_answer_release(self):
        conversation = self.conversation()
        def revoke(*args):
            with closing(self.chat.connect()) as connection, connection:
                connection.execute('UPDATE service_settings SET members = ? WHERE id = ?', ('[]', self.service_id))
            return 'Answer [1]'
        with patch.object(GenerationService, 'generate', side_effect=revoke):
            self.assertEqual(self.ask(conversation).status_code, 403)
        with closing(self.chat.connect()) as connection:
            self.assertEqual(connection.execute('SELECT status FROM chat_messages').fetchone()[0], 'failed')

    def test_abandoned_request_can_resume(self):
        conversation = self.conversation()
        request_id = str(uuid4())
        old_id = str(uuid4())
        with closing(self.chat.connect()) as connection, connection:
            connection.execute("INSERT INTO chat_messages (id, conversation_id, client_request_id, question, status, created_at) VALUES (?, ?, ?, ?, 'processing', '2020-01-01T00:00:00+00:00')",
                               (old_id, conversation, request_id, 'How much vacation?'))
        with patch.object(GenerationService, 'generate', return_value='Answer [1]'):
            response = self.ask(conversation, request_id)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotEqual(response.json()['id'], old_id)
