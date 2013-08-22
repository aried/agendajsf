package com.aried.agenda.util;

import javax.persistence.EntityManager;
import javax.persistence.EntityManagerFactory;
import javax.persistence.Persistence;

import com.aried.agenda.model.Contato;
import com.aried.agenda.model.Usuario;

public class PopulaBD {

	public static void main(String[] args) {
		EntityManagerFactory factory = Persistence
				.createEntityManagerFactory("agenda");
		EntityManager manager = factory.createEntityManager();

		Contato contato = new Contato();
		contato.setNome("João da Silva");
		contato.setEmail("joao@gmail.com");
		contato.setTelefone("+55 (016) 9115-2533");

		manager.getTransaction().begin();
		manager.persist(contato);
		manager.getTransaction().commit();
		manager.close();
		factory.close();
	}
}
