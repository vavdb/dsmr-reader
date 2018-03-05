# Generated by Django 2.0.2 on 2018-03-05 19:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dsmr_mqtt', '0001_mqtt_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='JSONDayTotalsMQTTSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False, help_text='Whether the day totals are sent to the broker, in JSON format.', verbose_name='Enabled')),
                ('topic', models.CharField(default='dsmr/day-totals', help_text='The topic to send the JSON formatted message to.', max_length=256, verbose_name='Topic path')),
                ('formatting', models.TextField(default='\n[mapping]\n# DATA = JSON FIELD\nelectricity1 = electricity1\nelectricity2 = electricity2\nelectricity1_returned = electricity1_returned\nelectricity2_returned = electricity2_returned\nelectricity_merged = electricity_merged\nelectricity_returned_merged = electricity_returned_merged\nelectricity1_cost = electricity1_cost\nelectricity2_cost = electricity2_cost\nelectricity_cost_merged = electricity_cost_merged\n\n# Gas (if any)\ngas = gas\ngas_cost = gas_cost\ntotal_cost = total_cost\n\n# Your energy supplier prices (if set)\nenergy_supplier_price_electricity_delivered_1 = energy_supplier_price_electricity_delivered_1\nenergy_supplier_price_electricity_delivered_2 = energy_supplier_price_electricity_delivered_2\nenergy_supplier_price_electricity_returned_1 = energy_supplier_price_electricity_returned_1\nenergy_supplier_price_electricity_returned_2 = energy_supplier_price_electricity_returned_2\nenergy_supplier_price_gas = energy_supplier_price_gas\n', help_text='Maps the field names used in the JSON message sent to the broker.', verbose_name='Formatting')),
            ],
            options={
                'default_permissions': (),
                'verbose_name': 'MQTT JSON day totals configuration',
            },
        ),
    ]
