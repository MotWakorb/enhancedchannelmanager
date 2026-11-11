/* Bounded regressions for the retained 2.13 ABI. Run only in a disposable
 * native builder. Allocation/IO budgets stop the unfixed parser safely; these
 * tests do not depend on a stack overflow, an operator service or network IO.
 */
#include <libxml/catalog.h>
#include <libxml/parser.h>
#include <libxml/relaxng.h>
#include <libxml/xmlIO.h>
#include <libxml/xmlmemory.h>
#include <libxml/xmlerror.h>
#include <dlfcn.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int opens, closes, budget_hit, parse_errors, depth_errors;
static size_t live_allocations;
static unsigned allocation_calls;
static int allocation_budget;
static int chain_length = 3;
typedef union { max_align_t align; size_t size; } Allocation;
typedef struct { char text[512]; size_t pos; } Input;

static void *tracked_malloc(size_t n) {
    if (allocation_budget && ++allocation_calls > 300) {
        budget_hit = 1;
        return NULL;
    }
    Allocation *p = malloc(sizeof(*p) + n);
    if (!p) return NULL;
    p->size = n;
    ++live_allocations;
    return p + 1;
}
static void tracked_free(void *ptr) {
    if (!ptr) return;
    --live_allocations;
    free((Allocation *)ptr - 1);
}
static void *tracked_realloc(void *ptr, size_t n) {
    Allocation *p;
    if (!ptr) return tracked_malloc(n);
    p = realloc((Allocation *)ptr - 1, sizeof(*p) + n);
    if (!p) return NULL;
    p->size = n;
    return p + 1;
}
static char *tracked_strdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *copy = tracked_malloc(n);
    if (copy) memcpy(copy, s, n);
    return copy;
}

static int match(const char *url) { return strncmp(url, "proof:", 6) == 0; }
static void *open_input(const char *url) {
    Input *in;
    int index = 0;
    if (++opens > 1005) { budget_hit = 1; return NULL; }
    in = calloc(1, sizeof(*in));
    if (in == NULL) abort();
    sscanf(url, "proof:rng%d", &index);
    if (index < chain_length)
        snprintf(in->text, sizeof(in->text),
                 "<grammar xmlns='http://relaxng.org/ns/structure/1.0'>"
                 "<include href='proof:rng%d'/></grammar>", index + 1);
    else
        snprintf(in->text, sizeof(in->text),
                 "<grammar xmlns='http://relaxng.org/ns/structure/1.0'>"
                 "<start><element name='root'><empty/></element></start></grammar>");
    return in;
}
static int read_input(void *ctx, char *buf, int len) {
    Input *in = ctx;
    size_t n = strlen(in->text) - in->pos;
    if (n > (size_t)len) n = (size_t)len;
    memcpy(buf, in->text + in->pos, n);
    in->pos += n;
    return (int)n;
}
static int close_input(void *ctx) { ++closes; free(ctx); return 0; }
static void error(void *ctx, const xmlError *err) {
    (void)ctx;
    if (err->domain == XML_FROM_RELAXNGP && err->code == XML_RNGP_PARSE_ERROR)
        ++parse_errors;
    if (err->message && strstr(err->message, "inclusion recursion limit reached"))
        ++depth_errors;
}
static void quiet(void *ctx, const char *msg, ...) { (void)ctx; (void)msg; }

static int catalog_test(int which) {
    xmlCatalogPtr cat;
    xmlChar *value;
    int failed;
    allocation_budget = which == 1;
    cat = xmlLoadACatalog(which == 1 ? "proof-self.sgml" : "proof-cat0.sgml");
    allocation_budget = 0;
    if (cat == NULL) return 1;
    value = xmlACatalogResolveSystem(cat, BAD_CAST (which == 1 ? "key" : "tail"));
    failed = budget_hit || (which == 1 ? value == NULL : value != NULL);
    xmlFree(value);
    xmlFreeCatalog(cat);
    return failed;
}

static int rng_test(const char *name) {
    typedef int (*Setter)(xmlRelaxNGParserCtxtPtr, int);
    Setter setter = (Setter)dlsym(RTLD_DEFAULT, "xmlRelaxParserSetIncLImit");
    xmlRelaxNGParserCtxtPtr ctxt = xmlRelaxNGNewParserCtxt("proof:rng0");
    xmlRelaxNGPtr schema;
    int expected = strcmp(name, "rng-default") == 0 ||
                   strcmp(name, "rng-api-exact") == 0 ||
                   strcmp(name, "rng-api-reset") == 0 ||
                   strcmp(name, "rng-env-zero") == 0;
    int failed = 0;
    if (!ctxt) return 1;
    xmlRelaxNGSetParserStructuredErrors(ctxt, error, NULL);
    if (strcmp(name, "rng-api") == 0) {
        if (!setter || setter(NULL, 2) != -1 || setter(ctxt, -1) != -1 ||
            setter(ctxt, 2) != 0) failed = 1;
    } else if (strcmp(name, "rng-api-exact") == 0) {
        if (!setter || setter(ctxt, 3) != 0) failed = 1;
    } else if (strcmp(name, "rng-api-reset") == 0) {
        if (!setter || setter(ctxt, 2) != 0 || setter(ctxt, 0) != 0) failed = 1;
    }
    schema = xmlRelaxNGParse(ctxt);
    if ((schema != NULL) != expected) failed = 1;
    if (!expected && parse_errors == 0) failed = 1;
    if ((strcmp(name, "rng-env") == 0 || strcmp(name, "rng-api") == 0 ||
         strcmp(name, "rng-deep") == 0) &&
        depth_errors == 0) failed = 1;
    if ((strcmp(name, "rng-env") == 0 || strcmp(name, "rng-api") == 0) &&
        opens > 4) failed = 1;
    if (strcmp(name, "rng-deep") == 0 && opens != 1002) failed = 1;
    if (strcmp(name, "rng-invalid") == 0 && opens != 0) failed = 1;
    if (budget_hit) failed = 1;
    xmlRelaxNGFree(schema);
    xmlRelaxNGFreeParserCtxt(ctxt);
    return failed;
}

int main(int argc, char **argv) {
    int failed, round;
    if (argc != 2) return 2;
    if (strcmp(argv[1], "rng-env") == 0 || strcmp(argv[1], "rng-api") == 0)
        chain_length = 6;
    if (strcmp(argv[1], "rng-deep") == 0) chain_length = 1001;
    if (xmlMemSetup(tracked_free, tracked_malloc, tracked_realloc, tracked_strdup))
        return 2;
    for (round = 0; round < 32; ++round) {
        opens = closes = budget_hit = parse_errors = depth_errors = 0;
        allocation_calls = 0;
        xmlInitParser();
        xmlSetGenericErrorFunc(NULL, quiet);
        if (xmlRegisterInputCallbacks(match, open_input, read_input, close_input) < 0)
            return 2;
        if (strncmp(argv[1], "sgml-", 5) == 0)
            failed = catalog_test(strcmp(argv[1], "sgml-dedup") == 0 ? 1 : 2);
        else
            failed = rng_test(argv[1]);
        if (!budget_hit && opens != closes) failed = 1;
        printf("%s round=%d opens=%d closes=%d budget=%d parse_errors=%d depth_errors=%d %s\n",
               argv[1], round, opens, closes, budget_hit, parse_errors, depth_errors,
               failed ? "FAIL" : "PASS");
        xmlResetLastError();
        xmlCleanupParser();
        if (live_allocations != 0) {
            fprintf(stderr, "unreleased libxml allocations=%zu\n", live_allocations);
            return 1;
        }
        if (failed) return 1;
    }
    return 0;
}
