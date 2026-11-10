import hashlib
import json
import secrets
from datetime import timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import delete, update
from webauthn.helpers import bytes_to_base64url as b64
from app.database import SessionLocal
from app.models import Passkey, PasskeyChallenge, User, utcnow
from app.security import ratelimit
from tests.conftest import CSRF_HEADERS

H = {**CSRF_HEADERS, "Origin": "http://localhost", "Host": "localhost"}
B = "http://localhost/api/v1/auth"

@pytest.fixture
def browser(client):
    ratelimit._attempts.clear(); client.cookies.clear()
    assert client.post(B+'/login', headers=H, json={'username':'admin','password':'test-password-123'}).status_code == 200
    yield client
    with SessionLocal() as db:
        db.execute(delete(PasskeyChallenge)); db.execute(delete(Passkey))
        db.execute(update(User).where(User.username=='admin').values(is_active=True)); db.commit()
    client.cookies.clear(); ratelimit._attempts.clear()

def register(c, uv=True):
    opts=c.post(B+'/passkeys/register/options',headers=H,json={'name':'test','password':'test-password-123'})
    assert opts.status_code==200,opts.text
    o=opts.json(); key=ec.generate_private_key(ec.SECP256R1()); n=key.public_key().public_numbers(); cid=secrets.token_bytes(32)
    cose=cbor2.dumps({1:2,3:-7,-1:1,-2:n.x.to_bytes(32),-3:n.y.to_bytes(32)})
    auth=hashlib.sha256(b'localhost').digest()+bytes([0x45 if uv else 0x41])+bytes(4)+bytes(16)+len(cid).to_bytes(2)+cid+cose
    cd=json.dumps({'type':'webauthn.create','challenge':o['challenge'],'origin':'http://localhost','crossOrigin':False}).encode()
    body={'credential':{'id':b64(cid),'rawId':b64(cid),'type':'public-key','response':{'clientDataJSON':b64(cd),'attestationObject':b64(cbor2.dumps({'fmt':'none','attStmt':{},'authData':auth}))}}}
    return c.post(B+'/passkeys/register/verify',headers=H,json=body),key,cid,o['user']['id'],body

def assertion(c,key,cid,handle,case=''):
    opts=c.post(B+'/login/passkey/options',headers=H); assert opts.status_code==200,opts.text
    assert opts.json()['userVerification']=='required'
    auth=hashlib.sha256(b'localhost').digest()+bytes([1 if case=='uv' else 5])+(1).to_bytes(4)
    cd=json.dumps({'type':'webauthn.get','challenge':opts.json()['challenge'],'origin':'https://evil.example' if case=='origin' else 'http://localhost','crossOrigin':False}).encode()
    sig=key.sign(auth+hashlib.sha256(cd).digest(),ec.ECDSA(hashes.SHA256()))
    return {'credential':{'id':b64(cid),'rawId':b64(cid),'type':'public-key','response':{'clientDataJSON':b64(cd),'authenticatorData':b64(auth),'signature':b64(sig),'userHandle':handle}}}

def test_real_signature_login_single_use(browser):
    r,key,cid,handle,_=register(browser); assert r.status_code==200,r.text
    browser.post(B+'/logout',headers=H)
    body=assertion(browser,key,cid,handle)
    r=browser.post(B+'/login/passkey/verify',headers=H,json=body)
    assert r.status_code==200,r.text
    assert r.json()['username']=='admin'
    assert browser.get(B+'/me').status_code==200
    assert browser.post(B+'/login/passkey/verify',headers=H,json=body).status_code==400

@pytest.mark.parametrize('case',['uv','origin','handle','signature','cookie','expired','disabled','counter','revoked'])
def test_invalid_proof(browser,case):
    r,key,cid,handle,_=register(browser); assert r.status_code==200,r.text
    body=assertion(browser,key,cid,handle,case)
    if case=='handle': body['credential']['response']['userHandle']=b64(b'other')
    if case=='signature': body['credential']['response']['signature']=b64(bytes(64))
    if case=='cookie': browser.cookies.clear()
    with SessionLocal() as db:
        if case=='expired':db.execute(update(PasskeyChallenge).values(expires_at=utcnow()-timedelta(seconds=1)))
        if case=='disabled':db.execute(update(User).where(User.username=='admin').values(is_active=False))
        if case=='counter':db.execute(update(Passkey).values(sign_count=2))
        if case=='revoked':db.execute(delete(Passkey))
        db.commit()
    r=browser.post(B+'/login/passkey/verify',headers=H,json=body)
    assert r.status_code in (400,401),r.text

def test_registration_password_uv_session_replay(browser):
    assert browser.post(B+'/passkeys/register/options',headers=H,json={'name':'test','password':'wrong'}).status_code==400
    r,*_=register(browser,uv=False); assert r.status_code==400
    r,_,_,_,body=register(browser); assert r.status_code==200
    assert browser.post(B+'/passkeys/register/verify',headers=H,json=body).status_code==400
    browser.post(B+'/passkeys/register/options',headers=H,json={'name':'test','password':'test-password-123'})
    browser.post(B+'/login',headers=H,json={'username':'admin','password':'test-password-123'})
    assert browser.post(B+'/passkeys/register/verify',headers=H,json=body).status_code==400

def test_origin_csrf_limit_delete(browser):
    assert browser.post(B+'/login/passkey/options').status_code==403
    assert browser.post(B+'/login/passkey/options',headers={**H,'Origin':'https://evil.example'}).status_code==400
    assert browser.post('http://lan/api/v1/auth/login/passkey/options',headers={**CSRF_HEADERS,'Origin':'http://lan'}).status_code==400
    r,*_=register(browser); assert r.status_code==200
    keys=browser.get(B+'/passkeys').json(); assert 'public_key' not in keys[0]
    assert browser.delete(B+f"/passkeys/{keys[0]['id']}",headers=H).status_code==200
    assert browser.get(B+'/passkeys').json()==[]
    for _ in range(31):r=browser.post(B+'/login/passkey/options',headers=H)
    assert r.status_code==429

def test_totp_enrollment_cannot_be_bypassed(browser):
    import pyotp
    from sqlalchemy import select
    from app.auth import totp
    secret=pyotp.random_base32()
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username=='admin'))
        totp.store_secret(user,secret);user.totp_enabled=True;db.commit()
    try:
        payload={'name':'test','password':'test-password-123'}
        assert browser.post(B+'/passkeys/register/options',headers=H,json=payload).status_code==400
        payload['totp_code']=pyotp.TOTP(secret).now()
        assert browser.post(B+'/passkeys/register/options',headers=H,json=payload).status_code==200
    finally:
        with SessionLocal() as db:
            db.execute(update(User).where(User.username=='admin').values(totp_enabled=False,totp_secret_encrypted=None));db.commit()


def test_another_user_cannot_list_or_delete_key(browser):
    from sqlalchemy import select
    from app.models import Role, UserSession
    from app.security.passwords import hash_password
    r,*_=register(browser);assert r.status_code==200
    key_id=browser.get(B+'/passkeys').json()[0]['id']
    with SessionLocal() as db:
        role=db.scalar(select(Role).where(Role.name=='viewer'))
        user=User(username='passkey-other',password_hash=hash_password('other-password-123'),role_id=role.id)
        db.add(user);db.commit();uid=user.id
    try:
        assert browser.post(B+'/login',headers=H,json={'username':'passkey-other','password':'other-password-123'}).status_code==200
        assert browser.get(B+'/passkeys').json()==[]
        assert browser.delete(B+f'/passkeys/{key_id}',headers=H).status_code==404
    finally:
        with SessionLocal() as db:
            db.execute(delete(UserSession).where(UserSession.user_id==uid))
            # Retain the audit trail; remove the disposable account via ORM-safe nulling.
            from app.models import AuditLog
            db.execute(update(AuditLog).where(AuditLog.user_id==uid).values(user_id=None))
            db.execute(delete(User).where(User.id==uid));db.commit()
