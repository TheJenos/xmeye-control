'use strict';

const crypto = require('crypto');

/**
 * Xiongmai "Sofia" password hash used by the DVRIP / NetSurveillance protocol.
 * Reference vector: sofiaHash('') === 'tlJwpbo6'.
 *
 * @param {string} password
 * @returns {string} 8-character hash
 */
function sofiaHash(password = '') {
  const chars =
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
  const md5 = crypto.createHash('md5').update(password, 'utf8').digest();
  let out = '';
  for (let i = 0; i < 16; i += 2) {
    const n = (md5[i] + md5[i + 1]) % 62;
    out += chars[n];
  }
  return out;
}

module.exports = { sofiaHash };
