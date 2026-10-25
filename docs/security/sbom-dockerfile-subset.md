# SBOM Dockerfile logical-line subset

Bead: `enhancedchannelmanager-5oa58`.

The source SBOM inventories the supported digest-pinned `FROM` and literal
`COPY --from=` image references, preserving previous-stage aliases and final
runtime identity. Default backslash-newline continuation removes the newline
without inserting a token separator, including splits inside command/flag/image
tokens. Physical lines end at LF, not Unicode line-separator characters.
Leading whitespace follows BuildKit's Go Unicode White_Space set, while token
canonicalization retains the existing ASCII whitespace rules.

Explicit `syntax`, `escape` and `check` parser directives remain unsupported and
fail closed, including backtick escape directives. Supporting a new directive is
not permission to broaden the separately frozen native/publication recipe.

Tests in `backend/tests/unit/test_generate_sbom.py` pin split-token external pulls,
Unicode-leading FROM/COPY, ordinary pinned images/stage aliases, comments/blank
continuation lines, CRLF, multiple trailing escapes and directive rejection.
Reference: [BuildKit parser](https://github.com/moby/buildkit/blob/master/frontend/dockerfile/parser/parser.go).

This is **not a general BuildKit grammar parser**. Quoted/escaped builder flags,
RUN mount image sources, ONBUILD triggers and heredocs need a separate reviewed
parsing/rejection policy before using this generic helper for arbitrary
Dockerfiles. In particular, heredoc text can resemble stage declarations. The
current publication path additionally freezes each reviewed Dockerfile's complete
recipe and rejects the unsupported examples found in review; generic helper
acceptance alone is not evidence of complete image-pull inventory or Dockerfile
validity. Those remaining forms are tracked in `enhancedchannelmanager-uzt5e`,
not silently declared safe.
