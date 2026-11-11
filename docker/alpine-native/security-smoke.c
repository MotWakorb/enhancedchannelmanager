/* Benign bounded regressions: no network loader or hostile media is executed. */
#include <libxml/parser.h>
#include <libxml/xinclude.h>
#include <speex/speex_bits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int loader_calls, missing_nonet;

static xmlParserInputPtr denied_loader(const char *url, const char *id,
                                       xmlParserCtxtPtr context) {
    (void)url;
    (void)id;
    ++loader_calls;
    if (!context || !(context->options & XML_PARSE_NONET)) missing_nonet = 1;
    return NULL;
}

static void require(int condition, const char *message) {
    if (!condition) {
        fprintf(stderr, "security smoke failed: %s\n", message);
        exit(1);
    }
}

static void nonet_case(const char *parse, int tree) {
    char text[512];
    int length = snprintf(text, sizeof(text),
        "<doc xmlns:xi='http://www.w3.org/2001/XInclude'>"
        "<xi:include href='http://example.invalid/offline' parse='%s'>"
        "<xi:fallback><p>offline</p></xi:fallback></xi:include></doc>", parse);
    require(length > 0 && (size_t)length < sizeof(text), "fixture size");
    xmlDocPtr doc = xmlReadMemory(text, length, "fixture.xml", NULL, XML_PARSE_NONET);
    require(doc != NULL, "parse fixture");
    loader_calls = missing_nonet = 0;
    int result = tree ? xmlXIncludeProcessTree(xmlDocGetRootElement(doc)) : xmlXIncludeProcess(doc);
    require(result >= 0 && loader_calls > 0 && !missing_nonet, "NONET propagation");
    xmlFreeDoc(doc);
}

int main(void) {
    xmlExternalEntityLoader original = xmlGetExternalEntityLoader();
    xmlSetExternalEntityLoader(denied_loader);
    nonet_case("text", 0);
    nonet_case("xml", 0);
    nonet_case("text", 1);
    nonet_case("xml", 1);
    xmlSetExternalEntityLoader(original);

    unsigned char buffer[16];
    memset(buffer, 0x5a, sizeof(buffer));
    SpeexBits bits;
    speex_bits_set_bit_buffer(&bits, buffer, 8);
    bits.nbBits = 32;
    speex_bits_read_whole_bytes(&bits, "abcdefgh", 8);
    for (int i = 8; i < 16; ++i) require(buffer[i] == 0x5a, "Speex external buffer canary");
    require(bits.nbBits <= 64, "Speex bounded bit count");
    speex_bits_destroy(&bits);
    puts("{\"xml_nonet_text\":true,\"xml_nonet_xml\":true,\"xml_tree_nonet_text\":true,\"xml_tree_nonet_xml\":true,\"speex_external_buffer_canary\":true}");
    return 0;
}
