"""CLI for SnapSync"""
import asyncio
import click
import logging
from pathlib import Path
from .config import Config
from .service import ServiceManager
from .database import BackupDatabase # Added import

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


@click.group()
@click.option('--config', default='config.yaml', help='Path to configuration file')
@click.pass_context
def cli(ctx, config):
    """SnapSync - SD Card Backup Service"""
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config


@cli.command()
@click.pass_context
def start(ctx):
    """Start the backup service"""
    config_path = ctx.obj['config_path']
    click.echo(f"Starting SnapSync service with config: {config_path}")

    async def run():
        config = Config.from_file(config_path)

        if not config.validate():
            click.echo("Configuration validation failed. Please check your config.", err=True)
            return

        service = ServiceManager(config)
        await service.start()

    asyncio.run(run())
    click.echo("\nSnapSync service shut down.")


@cli.command()
@click.pass_context
def status(ctx):
    """Show service status"""
    config_path = ctx.obj['config_path']

    async def get_status():
        config = Config.from_file(config_path)
        service = ServiceManager(config)

        # Initialize just the database to check status
        await service.database.initialize()

        active_session = await service.database.get_active_session()
        stats = await service.database.get_stats()

        await service.database.close()

        click.echo("\n=== SnapSync Status ===\n")

        if active_session:
            click.echo(f"Status: BACKING UP")
            click.echo(f"Device: {active_session['device_name']}")
            click.echo(f"Progress: {active_session['completed_files']}/{active_session['total_files']} files")
            click.echo(f"Failed: {active_session['failed_files']} files")
        else:
            click.echo(f"Status: IDLE")

        click.echo(f"\n=== Statistics ===\n")
        click.echo(f"Total files backed up: {stats.get('completed_files', 0)}")
        click.echo(f"Total size: {stats.get('total_size', 0)} bytes")
        click.echo(f"Failed files: {stats.get('failed_files', 0)}")
        click.echo(f"In progress: {stats.get('in_progress_files', 0)}")

    asyncio.run(get_status())


@cli.command()
@click.pass_context
def sessions(ctx):
    """List recent backup sessions"""
    config_path = ctx.obj['config_path']

    async def list_sessions():
        config = Config.from_file(config_path)
        service = ServiceManager(config)

        await service.database.initialize()

        recent_sessions = await service.database.get_recent_sessions(limit=10)

        await service.database.close()

        click.echo("\n=== Recent Backup Sessions ===\n")

        if not recent_sessions:
            click.echo("No sessions found.")
            return

        for session in recent_sessions:
            click.echo(f"Session ID: {session['session_id']}")
            click.echo(f"  Device: {session['device_name']}")
            click.echo(f"  Status: {session['status']}")
            click.echo(f"  Started: {session['start_time']}")
            click.echo(f"  Files: {session['completed_files']}/{session['total_files']}")
            click.echo(f"  Failed: {session['failed_files']}")
            click.echo("")

    asyncio.run(list_sessions())


@cli.command()
@click.option('--template', is_flag=True, help='Generate template configuration file')
@click.pass_context
def config(ctx, template):
    """Show or generate configuration"""
    if template:
        template_path = Path('config.yaml.example')
        if template_path.exists():
            target_path = Path('config.yaml')
            if target_path.exists():
                if not click.confirm(f"{target_path} already exists. Overwrite?"):
                    return

            import shutil
            shutil.copy(template_path, target_path)
            click.echo(f"Configuration template created at {target_path}")
            click.echo("Please edit the file and add your credentials.")
        else:
            click.echo("Template file not found.", err=True)
    else:
        config_path = ctx.obj['config_path']
        config_file = Path(config_path)

        if not config_file.exists():
            click.echo(f"Configuration file {config_path} not found.", err=True)
            click.echo("Run 'snapsync config --template' to generate a template.")
            return

        click.echo(f"\n=== Current Configuration ({config_path}) ===\n")
        with open(config_file, 'r') as f:
            click.echo(f.read())


@cli.command()
@click.pass_context
def test_connection(ctx):
    """Test connections to Immich and Unraid"""
    config_path = ctx.obj['config_path']

    async def test():
        config = Config.from_file(config_path)

        if not config.validate():
            click.echo("Configuration validation failed.", err=True)
            return

        click.echo("\n=== Testing Connections ===\n")

        # Test Immich
        if config.immich.enabled:
            click.echo("Testing Immich connection...")
            from .immich_client import ImmichClient

            immich = ImmichClient(config.immich.url, config.immich.api_key)
            await immich.initialize()

            if await immich.check_connection():
                click.echo("✓ Immich connection successful")
            else:
                click.echo("✗ Immich connection failed", err=True)

            await immich.close()

        # Test Unraid
        if config.unraid.enabled:
            click.echo("\nTesting Unraid connection...")
            from .unraid_client import UnraidClient

            unraid = UnraidClient(
                config.unraid.host,
                config.unraid.share,
                config.unraid.path,
                config.unraid.username,
                config.unraid.password,
                config.unraid.protocol
            )

            try:
                await unraid.initialize()

                if await unraid.check_connection():
                    click.echo("✓ Unraid connection successful")
                else:
                    click.echo("✗ Unraid connection failed", err=True)

                await unraid.close()
            except Exception as e:
                click.echo(f"✗ Unraid connection failed: {e}", err=True)

        click.echo("\nConnection tests complete.")

    asyncio.run(test())


@cli.command()
@click.pass_context
def web(ctx):
    """Start web UI only"""
    config_path = ctx.obj['config_path']
    click.echo(f"Starting SnapSync web UI with config: {config_path}")

    config = Config.from_file(config_path)

    import uvicorn
    from .web_ui import create_app
    from .service import ServiceManager

    async def setup():
        service = ServiceManager(config)
        await service.database.initialize()
        return service

    service = asyncio.run(setup())
    app = create_app(service)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.service.web_ui_port,
        log_level="info"
    )


@cli.command()
@click.argument('path')
@click.pass_context
def backup(ctx, path):
    """Manually trigger backup for a specific directory"""
    config_path = ctx.obj['config_path']

    async def run_backup():
        config = Config.from_file(config_path)

        if not config.validate():
            click.echo("Configuration validation failed.", err=True)
            return

        from .service import ServiceManager

        service = ServiceManager(config)

        # Initialize only what we need
        await service.database.initialize()

        if config.immich.enabled:
            from .immich_client import ImmichClient
            service.immich_client = ImmichClient(
                config.immich.url,
                config.immich.api_key,
                config.immich.timeout
            )
            await service.immich_client.initialize()

        if config.unraid.enabled:
            from .unraid_client import UnraidClient
            service.unraid_client = UnraidClient(
                config.unraid.host,
                config.unraid.share,
                config.unraid.path,
                config.unraid.username,
                config.unraid.password,
                config.unraid.protocol
            )
            await service.unraid_client.initialize()

        from .backup_engine import BackupEngine
        service.backup_engine = BackupEngine(
            config,
            service.database,
            service.immich_client,
            service.unraid_client
        )

        click.echo(f"\nStarting backup for: {path}\n")

        try:
            session_id = await service.trigger_backup(path)
            click.echo(f"Backup started with session ID: {session_id}")

            # Wait for backup to complete
            while True:
                session = await service.database.get_session(session_id)
                if session['status'] in ['completed', 'completed_with_errors', 'failed']:
                    break

                click.echo(
                    f"Progress: {session['completed_files']}/{session['total_files']} files "
                    f"({session['failed_files']} failed)"
                )
                await asyncio.sleep(2)

            # Final status
            session = await service.database.get_session(session_id)
            click.echo(f"\n=== Backup Complete ===")
            click.echo(f"Status: {session['status']}")
            click.echo(f"Total files: {session['total_files']}")
            click.echo(f"Completed: {session['completed_files']}")
            click.echo(f"Failed: {session['failed_files']}")
            click.echo(f"Bytes transferred: {session['transferred_bytes']}")

        except Exception as e:
            click.echo(f"Error: {e}", err=True)
        finally:
            # Cleanup
            if service.immich_client:
                await service.immich_client.close()
            if service.unraid_client:
                await service.unraid_client.close()
            await service.database.close()

    asyncio.run(run_backup())


async def _reset_database_async(config_path: str):
    """Asynchronous function to reset the database."""
    config = Config.from_file(config_path)
    db = BackupDatabase(config.service.database_path)
    await db.initialize()
    await db.reset()
    await db.close()


@cli.command(name='reset-db')
@click.pass_context
def reset_db(ctx):
    """
    Delete all records from the database.

    This is a destructive operation. It will erase all backup history,
    allowing files to be re-uploaded from an SD card.
    """
    config_path = ctx.obj['config_path']

    if not click.confirm(
        "⚠️  Are you sure you want to delete all backup history? This cannot be undone.",
        abort=True
    ):
        return  # This line is technically not needed due to abort=True, but is good for clarity

    click.echo("Resetting database...")

    asyncio.run(_reset_database_async(config_path))
    click.echo("✅ Database has been reset.")


def main():
    """Entry point for the CLI"""
    cli(obj={})


if __name__ == '__main__':
    main()

