import logging
import os
import re
import tempfile
import jwt
import time
import requests
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext as _

from netbox.data_backends import DataBackend
from netbox.utils import register_data_backend
from utilities.constants import HTTP_PROXY_SUPPORTED_SCHEMAS, HTTP_PROXY_SUPPORTED_SOCK_SCHEMAS
from utilities.proxy import resolve_proxies
from utilities.socks import ProxyPoolManager
from .exceptions import SyncError

__all__ = (
    'GitBackend',
    'GitHubJWTBackend',
    'LocalBackend',
    'S3Backend',
)

logger = logging.getLogger('netbox.data_backends')


@register_data_backend()
class LocalBackend(DataBackend):
    name = 'local'
    label = _('Local')
    is_local = True

    @contextmanager
    def fetch(self):
        logger.debug("Data source type is local; skipping fetch")
        local_path = urlparse(self.url).path  # Strip file:// scheme

        yield local_path


class GitBase(DataBackend):

    def init_config(self):
        from dulwich.config import ConfigDict

        # Initialize backend config
        config = ConfigDict()
        self.socks_proxy = None

        # Apply HTTP proxy (if configured)
        proxies = resolve_proxies(url=self.url, context={'client': self}) or {}
        if proxy := proxies.get(self.url_scheme):
            if urlparse(proxy).scheme not in HTTP_PROXY_SUPPORTED_SCHEMAS:
                raise ImproperlyConfigured(f"Unsupported Git DataSource proxy scheme: {urlparse(proxy).scheme}")

            if self.url_scheme in ('http', 'https'):
                config.set("http", "proxy", proxy)
                if urlparse(proxy).scheme in HTTP_PROXY_SUPPORTED_SOCK_SCHEMAS:
                    self.socks_proxy = proxy

        return config

    @contextmanager
    def fetch(self):
        from dulwich import porcelain

        local_path = tempfile.TemporaryDirectory()

        clone_args = {
            "branch": self.params.get('branch'),
            "config": self.config,
            "errstream": porcelain.NoneStream(),
        }

        # Check if using SOCKS for proxy - if so, need to use custom pool_manager
        if self.socks_proxy:
            clone_args['pool_manager'] = ProxyPoolManager(self.socks_proxy)

        if self.url_scheme in ('http', 'https'):
            username, password = self._build_auth()
            if username:
                clone_args.update(
                    {
                        "username": username,
                        "password": password,
                    }
                )
        if self.url_scheme:
            clone_args["quiet"] = True
            clone_args["depth"] = 1

        logger.debug(f"Cloning git repo: {self.url}")
        try:
            porcelain.clone(self.url, local_path.name, **clone_args)
        except BaseException as e:
            raise SyncError(_("Fetching remote data failed ({name}): {error}").format(name=type(e).__name__, error=e))

        yield local_path.name

        local_path.cleanup()

    def _build_auth(self):
        """Not implemented in base class."""
        return None, None


@register_data_backend()
class GitBackend(GitBase):
    name = 'git'
    label = 'Git'
    parameters = {
        'username': forms.CharField(
            required=False,
            label=_('Username'),
            widget=forms.TextInput(attrs={'class': 'form-control'}),
            help_text=_("Only used for cloning with HTTP(S)"),
        ),
        'password': forms.CharField(
            required=False,
            label=_('Password'),
            widget=forms.TextInput(attrs={'class': 'form-control'}),
            help_text=_("Only used for cloning with HTTP(S)"),
        ),
        'branch': forms.CharField(
            required=False,
            label=_('Branch'),
            widget=forms.TextInput(attrs={'class': 'form-control'})
        )
    }
    sensitive_parameters = ['password']

    def _build_auth(self):
        return self.params.get('username'), self.params.get('password')


@register_data_backend()
class GitHubJWTBackend(GitBase):
    name = 'github-jwt'
    label = 'GitHub (JWT)'
    parameters = {
        # Maybe a file upload field would be better here?
        'jwt_private_key': forms.CharField(
            required=False,
            label=_('JWT Private Key'),
            widget=forms.Textarea(attrs={'class': 'form-control'}),
            help_text=_("The private key of the GitHub App in PEM format."),
        ),
        'access_token_url': forms.CharField(
            required=False,
            label=_('Access Token API URL'),
            # GitHub Cloud/EMU
            initial='https://api.github.com/app/installations/{INSTALLATION ID HERE}/access_tokens',
            # GitHub Enterprise Server
            # initial='https://{hostname}/api/v3/app/installations/{INSTALLATION ID HERE}/access_tokens',
            widget=forms.TextInput(attrs={'class': 'form-control'}),
            help_text=_("The URL for the access token API."),
        ),
        'app_id': forms.CharField(
            required=False,
            label=_('App ID'),
            widget=forms.TextInput(attrs={'class': 'form-control'}),
            help_text=_("The ID of the GitHub App."),
        ),
        'branch': forms.CharField(
            required=False,
            label=_('Branch'),
            widget=forms.TextInput(attrs={'class': 'form-control'})
        ),
    }
    sensitive_parameters = ['jwt_private_key']

    def _build_auth(self):
        time_since_epoch_in_seconds = int(time.time())
        payload = {
            "iat": time_since_epoch_in_seconds - 10,
            "exp": time_since_epoch_in_seconds + 9 * 60,
            "iss": self.params.get('app_id'),
            "alg": "RS256",
        }
        try:
            encoded_jwt = jwt.encode(payload, self.params.get('jwt_private_key'), algorithm="RS256")
        except Exception as e:
            raise SyncError(_("Processing or encoding GitHub JWT token failed: {error}").format(error=e))

        if not isinstance(encoded_jwt, str):
            encoded_jwt = encoded_jwt.decode("utf-8")
        headers = {
            "Authorization": "Bearer " + encoded_jwt,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            r = requests.post(self.params.get('access_token_url'),
                              headers=headers,
                              proxies=resolve_proxies(url=self.params.get('access_token_url'),
                                                      context={'client': self}))
            r.raise_for_status()
            app_data = r.json()
            return self.params.get("app_id"), app_data["token"]
        except Exception as e:
            raise SyncError(_("Error fetching access token: {error}").format(error=e))


@register_data_backend()
class S3Backend(DataBackend):
    name = 'amazon-s3'
    label = 'Amazon S3'
    parameters = {
        'aws_access_key_id': forms.CharField(
            label=_('AWS access key ID'),
            widget=forms.TextInput(attrs={'class': 'form-control'})
        ),
        'aws_secret_access_key': forms.CharField(
            label=_('AWS secret access key'),
            widget=forms.TextInput(attrs={'class': 'form-control'})
        ),
    }
    sensitive_parameters = ['aws_secret_access_key']

    REGION_REGEX = r's3\.([a-z0-9-]+)\.amazonaws\.com'

    def init_config(self):
        from botocore.config import Config as Boto3Config

        # Initialize backend config
        return Boto3Config(
            proxies=resolve_proxies(url=self.url, context={'client': self}),
        )

    @contextmanager
    def fetch(self):
        import boto3

        local_path = tempfile.TemporaryDirectory()

        # Initialize the S3 resource and bucket
        aws_access_key_id = self.params.get('aws_access_key_id')
        aws_secret_access_key = self.params.get('aws_secret_access_key')
        s3 = boto3.resource(
            's3',
            region_name=self._region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            config=self.config,
            endpoint_url=self._endpoint_url
        )
        bucket = s3.Bucket(self._bucket_name)

        # Download all files within the specified path
        for obj in bucket.objects.filter(Prefix=self._remote_path):
            local_filename = os.path.join(local_path.name, obj.key)
            # Build local path
            Path(os.path.dirname(local_filename)).mkdir(parents=True, exist_ok=True)
            bucket.download_file(obj.key, local_filename)

        yield local_path.name

        local_path.cleanup()

    @property
    def _region_name(self):
        domain = urlparse(self.url).netloc
        if m := re.match(self.REGION_REGEX, domain):
            return m.group(1)
        return None

    @property
    def _bucket_name(self):
        url_path = urlparse(self.url).path.lstrip('/')
        return url_path.split('/')[0]

    @property
    def _endpoint_url(self):
        url_path = urlparse(self.url)
        return url_path._replace(params="", fragment="", query="", path="").geturl()

    @property
    def _remote_path(self):
        url_path = urlparse(self.url).path.lstrip('/')
        if '/' in url_path:
            return url_path.split('/', 1)[1]
        return ''
