# Security

Please report security problems privately to anantoni@sourcemined.com, not in a public issue.
You will get an answer within a week.

Marauder reads untrusted input (game executables and archives). A crash, hang or excessive memory use
on a crafted file is a bug worth reporting; code execution from a crafted file is a security issue.

AES keys you pass with `--aes-key` or `--aes-keys-file` are used in memory only and are never written
to the output, the report or the screen. If you find a case where a key is written anywhere, please
report it.
