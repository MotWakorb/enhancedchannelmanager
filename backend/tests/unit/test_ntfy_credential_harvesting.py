from credential_sentinel import collect_credential_values


def test_ntfy_topic_is_harvested_as_protected_only_with_ntfy_context():
    ntfy_topic = "private-ntfy-topic"
    ordinary_topic = "ordinary-topic"

    secrets, identities = collect_credential_values({
        "alert_methods": [
            {"method_type": "ntfy", "config": {"topic": ntfy_topic}},
            {"method_type": "webhook", "config": {"topic": ordinary_topic}},
        ]
    })

    assert ntfy_topic in secrets
    assert ordinary_topic not in secrets
    assert ordinary_topic not in identities
