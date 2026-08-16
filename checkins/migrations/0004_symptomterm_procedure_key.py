from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("checkins", "0003_alter_checkinphoto_image"),
    ]

    operations = [
        migrations.AlterField(
            model_name="symptomterm",
            name="key",
            field=models.CharField(max_length=20),
        ),
        migrations.AlterUniqueTogether(
            name="symptomterm",
            unique_together={("procedure_type", "key")},
        ),
    ]
