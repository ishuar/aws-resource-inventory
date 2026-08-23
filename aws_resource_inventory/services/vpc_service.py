"""
VPC Service Scanner
------------------

Scans VPC resources: VPCs, subnets, NAT gateways, internet gateways,
route tables, DHCP options, VPC peering connections, and VPC endpoints.
Tag-based filtering is handled by the Resource Groups API at the main
scanner level.

Fully declarative: every resource type is one paginated describe call,
so the whole scan is a Describe spec executed by the shared engine.
? Documentation: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html
"""

from typing import Any

from aws_resource_inventory.lib.clients import get_scan_client
from aws_resource_inventory.lib.engine import Describe, ScanResult, scan_keyed
from aws_resource_inventory.lib.logging import get_logger
from aws_resource_inventory.lib.records import CallerIdentity, Resource

logger = get_logger()

VPC_SPECS: dict[str, Describe] = {
    "vpcs": Describe("describe_vpcs", "Vpcs"),
    "subnets": Describe("describe_subnets", "Subnets"),
    "internet_gateways": Describe("describe_internet_gateways", "InternetGateways"),
    "route_tables": Describe("describe_route_tables", "RouteTables"),
    "nat_gateways": Describe("describe_nat_gateways", "NatGateways"),
    "dhcp_options": Describe("describe_dhcp_options", "DhcpOptions"),
    "vpc_peering_connections": Describe(
        "describe_vpc_peering_connections", "VpcPeeringConnections"
    ),
    "vpc_endpoints": Describe("describe_vpc_endpoints", "VpcEndpoints"),
}


def scan_vpc(session: Any, region: str) -> ScanResult:
    """Scan all VPC resources in the region (no tag filtering)."""
    client = get_scan_client(session, "ec2", region)
    return scan_keyed(client, VPC_SPECS, service="vpc", region=region, max_workers=4)


def process_vpc_output(
    service_data: dict[str, Any],
    region: str,
    flattened_resources: list[Resource],
    identity: CallerIdentity,
) -> None:
    """Process VPC scan results for output formatting.

    Except for subnets, the EC2 API returns no ARNs for VPC resources,
    so ARNs are constructed with the documented type segments (AWS
    Service Authorization Reference); VPC resources live under the
    "ec2" ARN service.
    """

    def constructed_arn(arn_type_segment: str, resource_id: str) -> str:
        return (
            f"arn:{identity.partition}:ec2:{region}"
            f":{identity.account}:{arn_type_segment}/{resource_id}"
        )

    def skip_missing_id(resource_type: str) -> None:
        logger.warning(
            "Skipping %s in %s: the API response carries no id field",
            resource_type,
            region,
        )

    # VPCs
    for vpc in service_data.get("vpcs", []):
        vpc_id = vpc.get("VpcId")
        if not vpc_id:
            skip_missing_id("vpc")
            continue
        cidr_block = vpc.get("CidrBlock", "N/A")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=f"VPC-{cidr_block}",
                resource_type="vpc",
                resource_id=vpc_id,
                resource_arn=constructed_arn("vpc", vpc_id),
                arn_source="constructed",
            )
        )

    # Subnets
    for subnet in service_data.get("subnets", []):
        subnet_id = subnet.get("SubnetId")
        if not subnet_id:
            skip_missing_id("vpc:subnet")
            continue
        cidr_block = subnet.get("CidrBlock", "N/A")
        # describe_subnets is the one VPC call that returns an ARN; keep
        # it when present, construct the same documented format otherwise.
        subnet_arn = subnet.get("SubnetArn")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=f"Subnet-{cidr_block}",
                resource_type="vpc:subnet",
                resource_id=subnet_id,
                resource_arn=subnet_arn or constructed_arn("subnet", subnet_id),
                arn_source="observed" if subnet_arn else "constructed",
            )
        )

    # NAT Gateways
    for nat_gw in service_data.get("nat_gateways", []):
        nat_gw_id = nat_gw.get("NatGatewayId")
        if not nat_gw_id:
            skip_missing_id("vpc:nat_gateway")
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=nat_gw_id,
                resource_type="vpc:nat_gateway",
                resource_id=nat_gw_id,
                resource_arn=constructed_arn("natgateway", nat_gw_id),
                arn_source="constructed",
            )
        )

    # Internet Gateways
    for igw in service_data.get("internet_gateways", []):
        igw_id = igw.get("InternetGatewayId")
        if not igw_id:
            skip_missing_id("vpc:internet_gateway")
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=igw_id,
                resource_type="vpc:internet_gateway",
                resource_id=igw_id,
                resource_arn=constructed_arn("internet-gateway", igw_id),
                arn_source="constructed",
            )
        )

    # Route Tables
    for rt in service_data.get("route_tables", []):
        rt_id = rt.get("RouteTableId")
        if not rt_id:
            skip_missing_id("vpc:route_table")
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=rt_id,
                resource_type="vpc:route_table",
                resource_id=rt_id,
                resource_arn=constructed_arn("route-table", rt_id),
                arn_source="constructed",
            )
        )

    # DHCP Options
    for dhcp in service_data.get("dhcp_options", []):
        dhcp_id = dhcp.get("DhcpOptionsId")
        if not dhcp_id:
            skip_missing_id("vpc:dhcp_options")
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=dhcp_id,
                resource_type="vpc:dhcp_options",
                resource_id=dhcp_id,
                resource_arn=constructed_arn("dhcp-options", dhcp_id),
                arn_source="constructed",
            )
        )

    # VPC Peering Connections
    for peering in service_data.get("vpc_peering_connections", []):
        peering_id = peering.get("VpcPeeringConnectionId")
        if not peering_id:
            skip_missing_id("vpc:peering_connection")
            continue

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=peering_id,
                resource_type="vpc:peering_connection",
                resource_id=peering_id,
                resource_arn=constructed_arn("vpc-peering-connection", peering_id),
                arn_source="constructed",
            )
        )

    # VPC Endpoints
    for endpoint in service_data.get("vpc_endpoints", []):
        endpoint_id = endpoint.get("VpcEndpointId")
        if not endpoint_id:
            skip_missing_id("vpc:endpoint")
            continue
        service_name = endpoint.get("ServiceName", "N/A")

        flattened_resources.append(
            Resource(
                region=region,
                resource_name=f"{endpoint_id}-{service_name.split('.')[-1] if service_name != 'N/A' else 'unknown'}",
                resource_type="vpc:endpoint",
                resource_id=endpoint_id,
                resource_arn=constructed_arn("vpc-endpoint", endpoint_id),
                arn_source="constructed",
            )
        )
