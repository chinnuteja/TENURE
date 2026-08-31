"""Create TENURE's Model Armor template through the required regional endpoint."""

from __future__ import annotations

import argparse

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import NotFound
from google.cloud import modelarmor_v1


def build_template() -> modelarmor_v1.Template:
    pi_settings = modelarmor_v1.PiAndJailbreakFilterSettings
    uri_settings = modelarmor_v1.MaliciousUriFilterSettings
    return modelarmor_v1.Template(
        filter_config=modelarmor_v1.FilterConfig(
            pi_and_jailbreak_filter_settings=(
                pi_settings(
                    filter_enforcement=pi_settings.PiAndJailbreakFilterEnforcement.ENABLED,
                    confidence_level=(
                        modelarmor_v1.DetectionConfidenceLevel.MEDIUM_AND_ABOVE
                    ),
                )
            ),
            malicious_uri_filter_settings=uri_settings(
                filter_enforcement=uri_settings.MaliciousUriFilterEnforcement.ENABLED
            ),
        )
    )


def ensure_template(project: str, location: str, template_id: str) -> str:
    client = modelarmor_v1.ModelArmorClient(
        transport="rest",
        client_options=ClientOptions(
            api_endpoint=f"modelarmor.{location}.rep.googleapis.com"
        ),
    )
    parent = f"projects/{project}/locations/{location}"
    name = f"{parent}/templates/{template_id}"
    try:
        return client.get_template(name=name).name
    except NotFound:
        response = client.create_template(
            request=modelarmor_v1.CreateTemplateRequest(
                parent=parent,
                template_id=template_id,
                template=build_template(),
            )
        )
        return response.name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--template", default="tenure-untrusted-input")
    args = parser.parse_args()
    print(ensure_template(args.project, args.location, args.template))


if __name__ == "__main__":
    main()
