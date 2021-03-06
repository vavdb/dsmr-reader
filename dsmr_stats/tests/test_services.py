from unittest import mock
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from dsmr_backend.tests.mixins import InterceptStdoutMixin
from dsmr_consumption.models.consumption import ElectricityConsumption, GasConsumption
from dsmr_consumption.models.energysupplier import EnergySupplierPrice
from dsmr_stats.models.statistics import DayStatistics, HourStatistics, ElectricityStatistics
from dsmr_datalogger.models.reading import DsmrReading
import dsmr_backend.services.backend
import dsmr_stats.services
from dsmr_consumption.models.settings import ConsumptionSettings


class TestServices(InterceptStdoutMixin, TestCase):
    fixtures = ['dsmr_stats/electricity-consumption.json', 'dsmr_stats/gas-consumption.json']

    def _get_statistics_dict(self, target_date):
        """ Used multiple times to setup proper day statistics data. """
        return {
            'day': target_date.date(),
            'total_cost': 39,
            'electricity1': 100,
            'electricity1_cost': 1,
            'electricity1_returned': 11,
            'electricity2': 200,
            'electricity2_cost': 2,
            'electricity2_returned': 22,
            'gas': 300,
            'gas_cost': 3,
        }

    @mock.patch('django.utils.timezone.now')
    def test_analyze_service(self, now_mock):
        self.assertTrue(ElectricityConsumption.objects.exists())
        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

        # This should delay statistics generation. Because day has not yet passed.
        now_mock.return_value = timezone.make_aware(timezone.datetime(2015, 12, 12, hour=1, minute=5))
        dsmr_stats.services.analyze()

        self.assertEqual(DayStatistics.objects.count(), 0)
        self.assertEqual(HourStatistics.objects.count(), 0)

        # Still too soon.
        now_mock.return_value = timezone.make_aware(timezone.datetime(2015, 12, 13, hour=1, minute=5))
        dsmr_stats.services.analyze()

        if dsmr_backend.services.backend.get_capabilities(capability='gas'):
            self.assertEqual(DayStatistics.objects.count(), 0)
            self.assertEqual(HourStatistics.objects.count(), 0)
        else:
            self.assertEqual(DayStatistics.objects.count(), 1)
            self.assertEqual(HourStatistics.objects.count(), 3)

        # Now we exceed the delay threshold, causing stats to be generated.
        now_mock.return_value += timezone.timedelta(minutes=15)
        dsmr_stats.services.analyze()

        if dsmr_backend.services.backend.get_capabilities(capability='gas'):
            self.assertEqual(DayStatistics.objects.count(), 1)
            self.assertEqual(HourStatistics.objects.count(), 3)
        else:
            self.assertEqual(DayStatistics.objects.count(), 2)
            self.assertEqual(HourStatistics.objects.count(), 4)

        # Second run should skip first day.
        dsmr_stats.services.analyze()
        self.assertEqual(DayStatistics.objects.count(), 2)
        self.assertEqual(HourStatistics.objects.count(), 4)

        # Third run should have no effect, as our fixtures are limited to a few days.
        dsmr_stats.services.analyze()
        self.assertEqual(DayStatistics.objects.count(), 2)
        self.assertEqual(HourStatistics.objects.count(), 4)

        # Without any data, fallback.
        for current_model in (DayStatistics, HourStatistics, ElectricityConsumption):
            current_model.objects.all().delete()

        self.assertFalse(ElectricityConsumption.objects.exists())
        dsmr_stats.services.analyze()
        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

    @mock.patch('django.utils.timezone.now')
    def test_analyze_service_block(self, now_mock):
        """ Checks whether unprocessed readings block statistics generation. """
        now_mock.return_value = timezone.make_aware(timezone.datetime(2015, 12, 13, hour=3))

        self.assertTrue(ElectricityConsumption.objects.exists())
        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

        # Verify block for unprocessed readings later on.
        DsmrReading.objects.create(
            timestamp=timezone.now() - timezone.timedelta(hours=12),
            electricity_delivered_1=1,
            electricity_returned_1=1,
            electricity_delivered_2=1,
            electricity_returned_2=1,
            electricity_currently_delivered=1,
            electricity_currently_returned=1,
            processed=False,
        )
        self.assertTrue(DsmrReading.objects.unprocessed().exists())

        dsmr_stats.services.analyze()

        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

        # Try again, without any blocking readings left.
        DsmrReading.objects.unprocessed().delete()
        self.assertFalse(DsmrReading.objects.unprocessed().exists())

        dsmr_stats.services.analyze()

        if dsmr_backend.services.backend.get_capabilities('any'):
            self.assertTrue(DayStatistics.objects.exists())
            self.assertTrue(HourStatistics.objects.exists())

    def test_create_hourly_gas_statistics_dsmr4(self):
        if not dsmr_backend.services.backend.get_capabilities(capability='gas'):
            return self.skipTest('No gas')

        day_start = timezone.make_aware(timezone.datetime(2015, 12, 12, hour=0))
        GasConsumption.objects.filter(pk__in=(32, 33)).delete()  # Pretend we only have 1 gas reading per hour.

        self.assertFalse(HourStatistics.objects.exists())
        dsmr_stats.services.create_hourly_statistics(hour_start=day_start)
        self.assertEqual(HourStatistics.objects.count(), 1)

        stats = HourStatistics.objects.get()
        self.assertEqual(stats.gas, Decimal('0.509'))

    def test_create_hourly_gas_statistics_dsmr5(self):
        if not dsmr_backend.services.backend.get_capabilities(capability='gas'):
            return self.skipTest('No gas')

        day_start = timezone.make_aware(timezone.datetime(2015, 12, 12, hour=0))
        self.assertFalse(HourStatistics.objects.exists())
        dsmr_stats.services.create_hourly_statistics(hour_start=day_start)
        self.assertEqual(HourStatistics.objects.count(), 1)

        stats = HourStatistics.objects.get()
        self.assertEqual(stats.gas, Decimal('0.125'))

    def test_create_hourly_statistics_integrity(self):
        day_start = timezone.make_aware(timezone.datetime(2015, 12, 13, hour=0))
        ec_kwargs = {
            'delivered_1': 0,
            'returned_1': 0,
            'delivered_2': 0,
            'returned_2': 0,
            'currently_delivered': 0,
            'currently_returned': 0,
        }
        ElectricityConsumption.objects.create(read_at=day_start, **ec_kwargs)

        self.assertFalse(HourStatistics.objects.exists())
        dsmr_stats.services.create_hourly_statistics(hour_start=day_start)
        self.assertEqual(HourStatistics.objects.count(), 1)

        # Should NOT raise any exception.
        dsmr_stats.services.create_hourly_statistics(hour_start=day_start)

    def test_analyze_service_without_data(self):
        first_consumption = ElectricityConsumption.objects.all().order_by('read_at')[0]
        first_consumption.read_at = first_consumption.read_at + timezone.timedelta()
        dsmr_stats.services.analyze()

    @mock.patch('django.utils.timezone.now')
    def test_analyze_service_skip_current_day(self, now_mock):
        """ Tests whether analysis postpones current day. """
        now_mock.return_value = timezone.make_aware(timezone.datetime(2016, 1, 1))

        # Drop fixtures and create data of today.
        ElectricityConsumption.objects.all().delete()
        GasConsumption.objects.all().delete()

        ElectricityConsumption.objects.create(
            read_at=timezone.now(),
            delivered_1=0,
            returned_1=0,
            delivered_2=0,
            returned_2=0,
            currently_delivered=0,
            currently_returned=0,
        )

        GasConsumption.objects.create(
            read_at=timezone.now(),
            delivered=0,
            currently_delivered=0,
        )

        # Make sure we have valid source data, but no analysis yet.
        self.assertTrue(ElectricityConsumption.objects.exists())
        self.assertTrue(GasConsumption.objects.exists())
        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

        try:
            dsmr_stats.services.analyze()
        except LookupError:
            pass

        # Analysis should be skipped, as all source data is faked into being generated today.
        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

    def test_clear_statistics(self):
        # Prepare some test data that should be deleted.
        target_date = timezone.make_aware(timezone.datetime(2016, 1, 1, 12))
        statistics_dict = self._get_statistics_dict(target_date)
        DayStatistics.objects.create(**statistics_dict)

        HourStatistics.objects.create(
            hour_start=timezone.now(),
            electricity1=1,
            electricity2=0,
            electricity1_returned=0,
            electricity2_returned=0,
        )
        self.assertTrue(DayStatistics.objects.exists())
        self.assertTrue(HourStatistics.objects.exists())

        dsmr_stats.services.clear_statistics()

        self.assertFalse(DayStatistics.objects.exists())
        self.assertFalse(HourStatistics.objects.exists())

    @mock.patch('django.core.cache.cache.clear')
    @mock.patch('django.utils.timezone.now')
    def test_analyze_service_clear_cache(self, now_mock, clear_cache_mock):
        now_mock.return_value = timezone.make_aware(timezone.datetime(2016, 1, 1, hour=2))
        dsmr_stats.services.analyze()
        self.assertTrue(clear_cache_mock.called)

    @mock.patch('django.utils.timezone.now')
    def test_average_consumption_by_hour(self, now_mock):
        """ Test whether timezones are converted properly when grouping hours. """
        now_mock.return_value = timezone.make_aware(timezone.datetime(2016, 1, 25, 12))
        HourStatistics.objects.create(
            # This should be stored with local timezone, so +1.
            hour_start=timezone.make_aware(timezone.datetime(2016, 1, 1, 12)),
            electricity1=1,
            electricity2=0,
            electricity1_returned=0,
            electricity2_returned=0,
        )
        hour_stat = dsmr_stats.services.average_consumption_by_hour(max_weeks_ago=4)[0]

        # @see "Trends are always shown in UTC #76", only PostgreSQL can fix this.
        if connection.vendor == 'postgresql':
            # Regression, Django defaults to UTC, so '11' here.
            self.assertEqual(hour_stat['hour_start'], 12)
        else:
            return self.skipTest('Test cannot be fixed for backends other than PostgreSQL')

    def test_range_statistics(self):
        target_date = timezone.make_aware(timezone.datetime(2016, 1, 1, 12))
        statistics_dict = self._get_statistics_dict(target_date)
        DayStatistics.objects.create(**statistics_dict)

        # Fetch inside our expected range.
        statistics = dsmr_stats.services.range_statistics(
            start=target_date, end=target_date + timezone.timedelta(days=1)
        )
        self.assertEqual(statistics['total_cost'], 39)
        self.assertEqual(statistics['electricity1'], 100)
        self.assertEqual(statistics['electricity1_cost'], 1)
        self.assertEqual(statistics['electricity1_returned'], 11)
        self.assertEqual(statistics['electricity2'], 200)
        self.assertEqual(statistics['electricity2_cost'], 2)
        self.assertEqual(statistics['electricity2_returned'], 22)
        self.assertEqual(statistics['electricity_merged'], 300)
        self.assertEqual(statistics['electricity_cost_merged'], 3)
        self.assertEqual(statistics['electricity_returned_merged'], 33)
        self.assertEqual(statistics['gas'], 300)
        self.assertEqual(statistics['gas_cost'], 3)

        # Now we fetch one outside our range.
        no_statistics = dsmr_stats.services.range_statistics(
            target_date - timezone.timedelta(days=1), target_date
        )
        self.assertIsNone(no_statistics['total_cost'])

    def test_day_statistics(self):
        target_date = timezone.make_aware(timezone.datetime(2016, 1, 1, 12))

        # Create statistics for multiple days in month, and beyond.
        for x in range(-5, 5):
            DayStatistics.objects.create(
                **self._get_statistics_dict(target_date + timezone.timedelta(days=x))
            )

        data = dsmr_stats.services.day_statistics(target_date=target_date)
        daily = self._get_statistics_dict(target_date)

        self.assertEqual(data['total_cost'], daily['total_cost'])
        self.assertEqual(data['electricity1'], daily['electricity1'])
        self.assertEqual(data['electricity1_cost'], daily['electricity1_cost'])
        self.assertEqual(data['electricity1_returned'], daily['electricity1_returned'])
        self.assertEqual(data['electricity2'], daily['electricity2'])
        self.assertEqual(data['electricity2_cost'], daily['electricity2_cost'])
        self.assertEqual(data['electricity2_returned'], daily['electricity2_returned'])
        self.assertEqual(data['electricity_merged'], daily['electricity1'] + daily['electricity2'])
        self.assertEqual(data['electricity_cost_merged'], daily['electricity1_cost'] + daily['electricity2_cost'])
        self.assertEqual(
            data['electricity_returned_merged'], daily['electricity1_returned'] + daily['electricity2_returned']
        )
        self.assertEqual(data['gas'], daily['gas'])
        self.assertEqual(data['gas_cost'], daily['gas_cost'])

    def test_month_statistics(self):
        target_date = timezone.make_aware(timezone.datetime(2016, 1, 1, 12))

        # Create statistics for multiple days.
        for x in range(0, 40):
            DayStatistics.objects.create(
                **self._get_statistics_dict(target_date + timezone.timedelta(days=x))
            )

        data = dsmr_stats.services.month_statistics(target_date=target_date)
        daily = self._get_statistics_dict(target_date)
        days_in_month = 31  # Hardcoded January.

        # Now we just verify whether the expected amount of days is fetched and summarized.
        # Since January only has 31 days and we we've generated 40, we should multiply by 31.
        self.assertEqual(data['total_cost'], daily['total_cost'] * days_in_month)
        self.assertEqual(data['electricity1'], daily['electricity1'] * days_in_month)
        self.assertEqual(data['electricity1_cost'], daily['electricity1_cost'] * days_in_month)
        self.assertEqual(data['electricity1_returned'], daily['electricity1_returned'] * days_in_month)
        self.assertEqual(data['electricity2'], daily['electricity2'] * days_in_month)
        self.assertEqual(data['electricity2_cost'], daily['electricity2_cost'] * days_in_month)
        self.assertEqual(data['electricity2_returned'], daily['electricity2_returned'] * days_in_month)
        self.assertEqual(
            data['electricity_merged'], (daily['electricity1'] + daily['electricity2']) * days_in_month
        )
        self.assertEqual(
            data['electricity_cost_merged'], (daily['electricity1_cost'] + daily['electricity2_cost']) * days_in_month
        )
        self.assertEqual(
            data['electricity_returned_merged'],
            (daily['electricity1_returned'] + daily['electricity2_returned']) * days_in_month
        )
        self.assertEqual(data['gas'], daily['gas'] * days_in_month)
        self.assertEqual(data['gas_cost'], daily['gas_cost'] * days_in_month)

    def test_year_statistics(self):
        target_date = timezone.make_aware(timezone.datetime(2016, 1, 1, 12))

        # Create statistics for multiple days in year, and beyond.
        for x in range(-5, 400):
            DayStatistics.objects.create(
                **self._get_statistics_dict(target_date + timezone.timedelta(days=x))
            )

        data = dsmr_stats.services.year_statistics(target_date=target_date)
        daily = self._get_statistics_dict(target_date)
        days_in_year = 366  # Hardcoded leapyear 2016.

        # Now we just verify whether the expected amount of days is fetched and summarized.
        # Since January only has 31 days and we we've generated 40, we should multiply by 31.
        self.assertEqual(data['total_cost'], daily['total_cost'] * days_in_year)
        self.assertEqual(data['electricity1'], daily['electricity1'] * days_in_year)
        self.assertEqual(data['electricity1_cost'], daily['electricity1_cost'] * days_in_year)
        self.assertEqual(data['electricity1_returned'], daily['electricity1_returned'] * days_in_year)
        self.assertEqual(data['electricity2'], daily['electricity2'] * days_in_year)
        self.assertEqual(data['electricity2_cost'], daily['electricity2_cost'] * days_in_year)
        self.assertEqual(data['electricity2_returned'], daily['electricity2_returned'] * days_in_year)
        self.assertEqual(
            data['electricity_merged'], (daily['electricity1'] + daily['electricity2']) * days_in_year
        )
        self.assertEqual(
            data['electricity_cost_merged'], (daily['electricity1_cost'] + daily['electricity2_cost']) * days_in_year
        )
        self.assertEqual(
            data['electricity_returned_merged'],
            (daily['electricity1_returned'] + daily['electricity2_returned']) * days_in_year
        )
        self.assertEqual(data['gas'], daily['gas'] * days_in_year)
        self.assertEqual(data['gas_cost'], daily['gas_cost'] * days_in_year)

    def test_electricity_tariff_percentage(self):
        target_date = timezone.make_aware(timezone.datetime(2016, 1, 1, 12))
        statistics_dict = self._get_statistics_dict(target_date)
        statistics_dict['electricity1'] = 5
        statistics_dict['electricity2'] = 15
        DayStatistics.objects.create(**statistics_dict)

        percentages = dsmr_stats.services.electricity_tariff_percentage(start_date=target_date.date())
        self.assertEqual(percentages['electricity1'], 25)
        self.assertEqual(percentages['electricity2'], 75)

        # Now try again without data.
        DayStatistics.objects.all().delete()
        percentages = dsmr_stats.services.electricity_tariff_percentage(start_date=target_date.date())

    @mock.patch('dsmr_stats.services.update_electricity_statistics')
    def test_dsmr_update_electricity_statistics_signal(self, service_mock):
        """ Test reading creation signal connects service. """
        self.assertFalse(service_mock.called)
        reading = DsmrReading.objects.create(
            timestamp=timezone.now(),
            electricity_delivered_1=0,
            electricity_returned_1=0,
            electricity_delivered_2=0,
            electricity_returned_2=0,
            electricity_currently_delivered=0,
            electricity_currently_returned=0,
        )
        service_mock.assert_called_once_with(reading=reading)

    @mock.patch('django.utils.timezone.now')
    def test_update_electricity_statistics(self, now_mock):
        now_mock.return_value = timezone.make_aware(timezone.datetime(2018, 1, 1, hour=0))
        stats = ElectricityStatistics.get_solo()
        self.assertIsNone(stats.highest_usage_l1_value)
        self.assertIsNone(stats.highest_usage_l2_value)
        self.assertIsNone(stats.highest_usage_l3_value)
        self.assertIsNone(stats.highest_return_l1_value)
        self.assertIsNone(stats.highest_return_l2_value)
        self.assertIsNone(stats.highest_return_l3_value)

        # This has to differ, to make sure the right timestamp is used.
        reading_timestamp = timezone.now()
        reading = DsmrReading.objects.create(
            timestamp=reading_timestamp,
            electricity_delivered_1=0,
            electricity_returned_1=0,
            electricity_delivered_2=0,
            electricity_returned_2=0,
            electricity_currently_delivered=0.6,
            electricity_currently_returned=1.6,
            phase_currently_delivered_l1=0.1,
            phase_currently_delivered_l2=0.2,
            phase_currently_delivered_l3=0.3,
            phase_currently_returned_l1=1.1,
            phase_currently_returned_l2=1.2,
            phase_currently_returned_l3=1.3,
        )

        # Alter time, processing of reading is later.
        now_mock.return_value = timezone.make_aware(timezone.datetime(2018, 1, 1, hour=12))
        self.assertNotEqual(reading_timestamp, timezone.now())
        dsmr_stats.services.update_electricity_statistics(reading=reading)

        # Should be updated now.
        stats = ElectricityStatistics.get_solo()
        self.assertEqual(stats.highest_usage_l1_value, Decimal('0.100'))
        self.assertEqual(stats.highest_usage_l2_value, Decimal('0.200'))
        self.assertEqual(stats.highest_usage_l3_value, Decimal('0.300'))
        self.assertEqual(stats.highest_return_l1_value, Decimal('1.100'))
        self.assertEqual(stats.highest_return_l2_value, Decimal('1.200'))
        self.assertEqual(stats.highest_return_l3_value, Decimal('1.300'))
        self.assertEqual(stats.highest_usage_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_usage_l2_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_usage_l3_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_return_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_return_l2_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_return_l3_timestamp, reading_timestamp)

    @mock.patch('django.utils.timezone.now')
    def test_update_electricity_statistics_single_phase(self, now_mock):
        ConsumptionSettings.objects.update(compactor_grouping_type=ConsumptionSettings.COMPACTOR_GROUPING_BY_READING)

        now_mock.return_value = timezone.make_aware(timezone.datetime(2018, 1, 1, hour=0))
        stats = ElectricityStatistics.get_solo()
        self.assertIsNone(stats.highest_usage_l1_value)
        self.assertIsNone(stats.highest_usage_l2_value)
        self.assertIsNone(stats.highest_usage_l3_value)
        self.assertIsNone(stats.highest_return_l1_value)
        self.assertIsNone(stats.highest_return_l2_value)
        self.assertIsNone(stats.highest_return_l3_value)
        self.assertIsNone(stats.lowest_usage_l1_value)
        self.assertIsNone(stats.lowest_usage_l2_value)
        self.assertIsNone(stats.lowest_usage_l3_value)

        # This has to differ, to make sure the right timestamp is used.
        reading_timestamp = timezone.now()
        reading = DsmrReading.objects.create(
            timestamp=reading_timestamp,
            electricity_delivered_1=0,
            electricity_returned_1=0,
            electricity_delivered_2=0,
            electricity_returned_2=0,
            electricity_currently_delivered=0.6,
            electricity_currently_returned=1.6,
            phase_currently_delivered_l1=0.1,
            phase_currently_returned_l1=1.1,
        )

        # Alter time, processing of reading is later.
        now_mock.return_value = timezone.make_aware(timezone.datetime(2018, 1, 1, hour=12))
        self.assertNotEqual(reading_timestamp, timezone.now())
        dsmr_stats.services.update_electricity_statistics(reading=reading)

        # Should be updated now.
        stats = ElectricityStatistics.get_solo()
        self.assertEqual(stats.highest_usage_l1_value, Decimal('0.100'))
        self.assertEqual(stats.highest_usage_l2_value, None)
        self.assertEqual(stats.highest_usage_l3_value, None)
        self.assertEqual(stats.highest_usage_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_usage_l2_timestamp, None)
        self.assertEqual(stats.highest_usage_l3_timestamp, None)

        self.assertEqual(stats.highest_return_l1_value, Decimal('1.100'))
        self.assertEqual(stats.highest_return_l2_value, None)
        self.assertEqual(stats.highest_return_l3_value, None)
        self.assertEqual(stats.highest_return_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_return_l2_timestamp, None)
        self.assertEqual(stats.highest_return_l3_timestamp, None)

        self.assertEqual(stats.lowest_usage_l1_value, Decimal('0.100'))
        self.assertEqual(stats.lowest_usage_l2_value, None)
        self.assertEqual(stats.lowest_usage_l3_value, None)
        self.assertEqual(stats.lowest_usage_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.lowest_usage_l2_timestamp, None)
        self.assertEqual(stats.lowest_usage_l3_timestamp, None)

        # Test a new low.
        reading2 = DsmrReading.objects.create(
            timestamp=reading_timestamp + timezone.timedelta(seconds=10),
            electricity_delivered_1=0,
            electricity_returned_1=0,
            electricity_delivered_2=0,
            electricity_returned_2=0,
            electricity_currently_delivered=0.6,
            electricity_currently_returned=1.6,
            phase_currently_delivered_l1=0.05,
            phase_currently_returned_l1=1.0,
        )
        dsmr_stats.services.update_electricity_statistics(reading=reading2)

        stats = ElectricityStatistics.get_solo()
        self.assertEqual(stats.highest_usage_l1_value, Decimal('0.100'))
        self.assertEqual(stats.highest_usage_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.highest_return_l1_value, Decimal('1.100'))
        self.assertEqual(stats.highest_return_l1_timestamp, reading_timestamp)
        self.assertEqual(stats.lowest_usage_l1_value, Decimal('0.050'))
        self.assertEqual(stats.lowest_usage_l1_timestamp, reading2.timestamp)

    def test_recalculate_prices(self):
        target_day = timezone.make_aware(timezone.datetime(2018, 1, 1))
        EnergySupplierPrice.objects.create(
            start=target_day.date(),
            end=target_day.date(),
            electricity_delivered_1_price=1,
            electricity_delivered_2_price=2,
            gas_price=5
        )
        DayStatistics.objects.create(
            day=target_day.date(),
            total_cost=12345,
            electricity1=10,
            electricity2=20,
            electricity1_cost=15,
            electricity2_cost=30,
            gas=1,
            gas_cost=7,
            electricity1_returned=0,
            electricity2_returned=0,
        )

        day_totals = DayStatistics.objects.all()[0]
        self.assertEqual(day_totals.electricity1_cost, 15)
        self.assertEqual(day_totals.electricity2_cost, 30)
        self.assertEqual(day_totals.gas_cost, 7)
        self.assertEqual(day_totals.total_cost, 12345)

        dsmr_stats.services.recalculate_prices()

        day_totals = DayStatistics.objects.all()[0]
        self.assertEqual(day_totals.electricity1_cost, 10)
        self.assertEqual(day_totals.electricity2_cost, 40)
        self.assertEqual(day_totals.gas_cost, 5)
        self.assertEqual(day_totals.total_cost, 55)


class TestServicesWithoutGas(TestServices):
    fixtures = ['dsmr_stats/electricity-consumption.json']
