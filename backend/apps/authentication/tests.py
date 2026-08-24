from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase


class RegistrationTests(APITestCase):
	endpoint = '/api/auth/register/'

	def valid_payload(self, **overrides):
		payload = {
			'email': 'nuevo@ejemplo.com',
			'password': 'UnaClaveSegura123!',
			'password_confirmation': 'UnaClaveSegura123!',
			'accepted_terms': True,
		}
		payload.update(overrides)
		return payload

	def test_registers_user_with_email_as_username(self):
		response = self.client.post(self.endpoint, self.valid_payload(), format='json')

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertTrue(User.objects.filter(username='nuevo@ejemplo.com').exists())

	def test_rejects_mismatched_passwords(self):
		response = self.client.post(
			self.endpoint,
			self.valid_payload(password_confirmation='OtraClave123!'),
			format='json'
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('password_confirmation', response.data)

	def test_rejects_duplicate_email(self):
		User.objects.create_user(
			username='existente@ejemplo.com',
			email='existente@ejemplo.com',
			password='UnaClaveSegura123!'
		)

		response = self.client.post(
			self.endpoint,
			self.valid_payload(email='EXISTENTE@EJEMPLO.COM'),
			format='json'
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn('email', response.data)
